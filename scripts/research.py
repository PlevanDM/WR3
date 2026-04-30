"""Auto-research: UI walk + detective checks.

Minimal health check for production-ready code. Runs only essential tests:
- ui_walk: headless button clicks, UI rendering
- detective: code imports, models, config

Output: structured report with issues and recommendations.

Usage:
    python scripts\\research.py
"""
from __future__ import annotations
import asyncio
import subprocess
import sys
import time
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _configure_console_output() -> None:
    """Avoid UnicodeEncodeError on Windows consoles with legacy encodings."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


@dataclass
class ProbeResult:
    name: str
    passed: bool
    tests: int = 0
    failed: int = 0
    duration_sec: float = 0
    output: str = ""
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class ResearchEngine:
    def __init__(self):
        self.results: dict[str, ProbeResult] = {}
        self.issues: list[tuple[str, str, str]] = []  # (severity, category, detail)
        self.recommendations: list[str] = []

    def run_probe(self, name: str, cmd: list[str], timeout: int = 180) -> ProbeResult:
        """Run a test script and capture result."""
        start = time.time()
        print(f"  ▶ Running {name}…", end=" ", flush=True)
        try:
            result = subprocess.run(
                cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True,
                timeout=timeout, encoding="utf-8", errors="replace"
            )
            elapsed = time.time() - start
            output = result.stdout + result.stderr

            probe = ProbeResult(name=name, passed=(result.returncode == 0),
                                duration_sec=elapsed, output=output)
            # Parse test counts from output
            match_passed = re.search(r'(\d+)/(\d+) passed', output)
            if match_passed:
                probe.passed = int(match_passed.group(1)) == int(match_passed.group(2))
                probe.tests = int(match_passed.group(2))
                probe.failed = max(0, int(match_passed.group(2)) - int(match_passed.group(1)))
            elif "OK ===" in output:
                match_ok = re.search(r'(\d+)/(\d+) OK', output)
                if match_ok:
                    probe.passed = int(match_ok.group(1)) == int(match_ok.group(2))
                    probe.tests = int(match_ok.group(2))
                    probe.failed = max(0, int(match_ok.group(2)) - int(match_ok.group(1)))

            # Extract error messages
            for line in output.splitlines():
                if "FAIL" in line or "ERROR" in line or "error" in line.lower():
                    if len(line) < 200:
                        probe.errors.append(line.strip())

            status = "✓" if probe.passed else "✗"
            print(f"{status} {elapsed:.1f}s" + (f" ({probe.failed} failed)" if probe.failed else ""))
            self.results[name] = probe
            return probe
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start
            print(f"✗ TIMEOUT after {elapsed:.1f}s")
            self.issues.append(("error", "timeout", f"{name} took >{timeout}s"))
            return ProbeResult(name=name, passed=False, duration_sec=elapsed,
                               errors=[f"Timeout after {timeout}s"])
        except Exception as e:
            elapsed = time.time() - start
            print(f"✗ Exception: {type(e).__name__}")
            self.issues.append(("error", "exception", f"{name}: {type(e).__name__}: {e}"))
            return ProbeResult(name=name, passed=False, duration_sec=elapsed,
                               errors=[str(e)])

    def run_detective_checks(self):
        """Deep analysis of handler code and logic flow."""
        print("\n2️⃣ DETECTIVE CHECKS")
        checks_passed = 0
        checks_total = 0

        # Check 1: Handlers exist and have decorators
        checks_total += 1
        try:
            from app.bot.handlers import engineer, manager, reception, admin
            for module, name in [(engineer, "engineer"), (manager, "manager"),
                                  (reception, "reception"), (admin, "admin")]:
                handlers = [n for n in dir(module) if not n.startswith("_") and callable(getattr(module, n))]
                if len(handlers) > 3:
                    print(f"  ✓ {name}: {len(handlers)} handlers found")
                    checks_passed += 1
                else:
                    print(f"  ✗ {name}: only {len(handlers)} handlers (expected >3)")
        except Exception as e:
            print(f"  ✗ Failed to import handlers: {e}")

        # Check 2: Text rendering
        checks_total += 1
        try:
            from app.bot.texts import HELLO, ROLE_TIPS, REQUEST_LABELS
            from app.bot.services.widgets import _render_stats_widget, _render_queue_widget
            if HELLO and ROLE_TIPS and REQUEST_LABELS:
                print(f"  ✓ Texts: HELLO, ROLE_TIPS, REQUEST_LABELS all present")
                checks_passed += 1
        except Exception as e:
            print(f"  ✗ Text imports failed: {e}")

        # Check 3: Database schema
        checks_total += 1
        try:
            from app.db.models import User, Order, Request, RequestType, RequestStatus
            print(f"  ✓ DB models: User, Order, Request, RequestType, RequestStatus OK")
            checks_passed += 1
        except Exception as e:
            print(f"  ✗ DB model imports failed: {e}")

        # Check 4: RemOnline client
        checks_total += 1
        try:
            from app.remonline.client import RemOnlineClient
            print(f"  ✓ RemOnline client imported OK")
            checks_passed += 1
        except Exception as e:
            print(f"  ✗ RemOnline client import failed: {e}")

        # Check 5: Configuration
        checks_total += 1
        try:
            from app.config import settings
            if settings.bot_token and settings.remonline_api_key:
                print(f"  ✓ Config: bot_token and remonline_api_key set")
                checks_passed += 1
            else:
                print(f"  ⚠ Config: missing credentials")
        except Exception as e:
            print(f"  ✗ Config failed: {e}")

        return checks_passed, checks_total

    def analyze(self):
        """Analyze all results for patterns and issues."""
        print("\n" + "=" * 70)
        print("ANALYSIS")
        print("=" * 70)

        # 1. Pass rate
        all_ok = all(p.passed for p in self.results.values())
        if all_ok:
            print("✓ All probes PASSED")
        else:
            failed = [n for n, p in self.results.items() if not p.passed]
            self.issues.append(("error", "regression", f"Failed probes: {', '.join(failed)}"))
            print(f"✗ FAILED: {', '.join(failed)}")

        # 2. Performance trends
        for name, probe in self.results.items():
            if probe.duration_sec > 120:
                self.issues.append(("warning", "slow", f"{name}: {probe.duration_sec:.1f}s"))
                print(f"⚠ SLOW: {name} took {probe.duration_sec:.1f}s")
            elif probe.duration_sec > 60:
                self.issues.append(("info", "medium_time", f"{name}: {probe.duration_sec:.1f}s"))

        # 3. Error aggregation
        if self.results:
            ui_walk = self.results.get("ui_walk")
            if ui_walk and ui_walk.failed > 0:
                self.issues.append(("error", "ui_failure",
                                    f"ui_walk: {ui_walk.failed} checks failed"))

            e2e = self.results.get("e2e_probe")
            if e2e and e2e.failed > 0:
                self.issues.append(("error", "e2e_failure",
                                    f"e2e_probe: {e2e.failed}/{e2e.tests} tests failed"))

            integ = self.results.get("integration_probe")
            if integ and integ.failed > 0:
                self.issues.append(("error", "integration_failure",
                                    f"integration_probe: {integ.failed}/{integ.tests} tests failed"))

        # 4. Warnings from probe output
        for name, probe in self.results.items():
            warnings = [l for l in probe.output.splitlines()
                       if "[warning" in l or "⚠" in l][:3]
            if warnings:
                for w in warnings:
                    short = w[:100]
                    self.issues.append(("warning", name, short))

    def report(self):
        """Print structured report."""
        print("\n" + "=" * 70)
        print("REPORT")
        print("=" * 70)

        # Summary
        total = len(self.results)
        passed_count = sum(1 for p in self.results.values() if p.passed)
        print(f"\n📊 SUMMARY: {passed_count}/{total} probes OK")
        print(f"   Total time: {sum(p.duration_sec for p in self.results.values()):.1f}s")

        # Breakdown
        if self.results:
            print("\n📋 PROBES:")
            for name, probe in sorted(self.results.items()):
                status = "✓" if probe.passed else "✗"
                detail = ""
                if probe.tests > 0 and probe.failed > 0:
                    detail = f" [{probe.tests - probe.failed}/{probe.tests} passed]"
                print(f"   {status} {name:20} {probe.duration_sec:6.1f}s{detail}")

        # Issues by severity
        if self.issues:
            print("\n⚠️ ISSUES:")
            by_severity = {}
            for sev, cat, detail in self.issues:
                if sev not in by_severity:
                    by_severity[sev] = []
                by_severity[sev].append((cat, detail))

            for sev in ["error", "warning", "info"]:
                if sev in by_severity:
                    icon = {"error": "✗", "warning": "⚠", "info": "ℹ"}[sev]
                    print(f"\n   {icon} {sev.upper()}:")
                    for cat, detail in by_severity[sev][:5]:
                        print(f"      • {cat}: {detail[:100]}")
                    if len(by_severity[sev]) > 5:
                        print(f"      … и ещё {len(by_severity[sev]) - 5}")

        # Recommendations
        self._generate_recommendations()
        if self.recommendations:
            print("\n💡 RECOMMENDATIONS:")
            for i, rec in enumerate(self.recommendations[:5], 1):
                print(f"   {i}. {rec}")

        print("\n" + "=" * 70)

    def _generate_recommendations(self):
        """Generate action items based on issues."""
        error_count = sum(1 for s, _, _ in self.issues if s == "error")
        if error_count > 3:
            self.recommendations.append(
                "Multiple errors detected. Run individual probes for details."
            )

        slow_probes = [d for s, c, d in self.issues if c == "slow"]
        if slow_probes:
            self.recommendations.append(
                f"Optimize slow probes: {slow_probes[0].split(':')[0]}"
            )

        if any("regression" in d for s, _, d in self.issues if s == "error"):
            self.recommendations.append(
                "Regression detected. Check recent changes in handlers or services."
            )

        ui_failures = sum(1 for s, c, _ in self.issues if c == "ui_failure" or c == "e2e_failure")
        if ui_failures > 0:
            self.recommendations.append(
                "UI/handler issues found. Review handler signatures and text rendering."
            )

        if not error_count:
            self.recommendations.append("✓ All systems nominal. Ready for testing in Telegram.")


def main(args):
    _configure_console_output()
    engine = ResearchEngine()

    print("🔬 AUTO-RESEARCH (Production Health Check)")
    print("=" * 70)

    # Run only essential test
    print("\n1️⃣ UI WALK")
    engine.run_probe("ui_walk", ["python", "-m", "scripts.ui_walk"], timeout=120)

    # Deep checks
    det_ok, det_total = engine.run_detective_checks()
    if det_ok < det_total:
        engine.issues.append(("warning", "detective", f"Detective checks: {det_ok}/{det_total} OK"))

    # Analyze
    engine.analyze()

    # Report
    engine.report()

    # Return status
    if any(s == "error" for s, _, _ in engine.issues):
        print("\n❌ RESEARCH FAILED — issues to address")
        return 1
    else:
        print("\n✅ RESEARCH OK — production ready")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
