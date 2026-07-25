#!/usr/bin/env python3
"""
Platform Readiness Validation Script
=====================================

Validates that the project is ready for multi-platform GPU support
(NVIDIA CUDA, Apple Metal, CPU).

Usage:
    python3 validate_platform_readiness.py [--verbose] [--platform cuda|metal|cpu|all]

Exit Codes:
    0 = Ready
    1 = Not ready (issues found)
    2 = Warnings (proceed with caution)
"""

import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

# ANSI colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
END = "\033[0m"
BOLD = "\033[1m"


class ValidationReport:
    """Validation report collector."""
    
    def __init__(self):
        self.checks: List[Tuple[str, bool, str]] = []
        self.errors = 0
        self.warnings = 0
        self.passed = 0
    
    def add_check(self, name: str, passed: bool, details: str = ""):
        """Add a check result."""
        self.checks.append((name, passed, details))
        if passed:
            self.passed += 1
        elif "warn" in details.lower():
            self.warnings += 1
        else:
            self.errors += 1
    
    def print_report(self):
        """Print formatted report."""
        print(f"\n{BOLD}Validation Report{END}\n")
        
        for name, passed, details in self.checks:
            status = f"{GREEN}✓{END}" if passed else f"{RED}✗{END}"
            print(f"{status} {name}")
            if details:
                print(f"  {details}")
        
        print(f"\n{BOLD}Summary{END}")
        print(f"  {GREEN}Passed:{END} {self.passed}")
        if self.warnings:
            print(f"  {YELLOW}Warnings:{END} {self.warnings}")
        if self.errors:
            print(f"  {RED}Errors:{END} {self.errors}")
        
        print()
        
        if self.errors == 0 and self.warnings == 0:
            print(f"{GREEN}✓ All checks passed! Ready for deployment.{END}\n")
            return 0
        elif self.errors == 0:
            print(f"{YELLOW}⚠ Warnings detected. Proceed with caution.{END}\n")
            return 2
        else:
            print(f"{RED}✗ Errors detected. Fix before deployment.{END}\n")
            return 1


def check_python_version(report: ValidationReport):
    """Check Python 3.10+."""
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 10)
    report.add_check(
        "Python Version",
        ok,
        f"Python {major}.{minor}" + ("" if ok else " (need 3.10+)")
    )


def check_pytorch_installed(report: ValidationReport):
    """Check PyTorch is installed."""
    try:
        import torch
        report.add_check("PyTorch", True, f"Version {torch.__version__}")
    except ImportError:
        report.add_check(
            "PyTorch",
            False,
            "Not installed. Run: pip install torch"
        )


def check_cuda_available(report: ValidationReport):
    """Check CUDA availability."""
    try:
        import torch
        cuda_available = torch.cuda.is_available()
        if cuda_available:
            device_name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            report.add_check(
                "NVIDIA CUDA GPU",
                True,
                f"{device_name} ({vram:.1f} GB VRAM)"
            )
        else:
            report.add_check(
                "NVIDIA CUDA GPU",
                True,
                "Not available (OK, Metal or CPU will be used) [WARN]"
            )
    except ImportError:
        report.add_check(
            "NVIDIA CUDA GPU",
            False,
            "PyTorch not installed"
        )


def check_metal_available(report: ValidationReport):
    """Check Metal GPU availability."""
    try:
        import torch
        if hasattr(torch.backends, "mps"):
            metal_available = torch.backends.mps.is_available()
            if metal_available:
                report.add_check(
                    "Apple Metal GPU",
                    True,
                    "Available on this Mac"
                )
            else:
                report.add_check(
                    "Apple Metal GPU",
                    True,
                    "Not available (probably running on Linux/Windows) [WARN]"
                )
        else:
            report.add_check(
                "Apple Metal GPU",
                True,
                "Not available (probably running on non-macOS) [WARN]"
            )
    except ImportError:
        report.add_check(
            "Apple Metal GPU",
            False,
            "PyTorch not installed"
        )


def check_platform_modules(report: ValidationReport):
    """Check platform abstraction modules exist."""
    root = Path(__file__).parent.parent
    
    platform_file = root / "app" / "platform.py"
    memory_file = root / "app" / "memory_budget.py"
    
    ok1 = platform_file.exists()
    ok2 = memory_file.exists()
    
    report.add_check(
        "Platform Detection Module (app/platform.py)",
        ok1,
        "Exists" if ok1 else "Missing"
    )
    report.add_check(
        "Memory Budget Module (app/memory_budget.py)",
        ok2,
        "Exists" if ok2 else "Missing"
    )


def check_platform_detection_works(report: ValidationReport):
    """Check platform detection can run."""
    try:
        from app.platform import detect_compute_device
        config = detect_compute_device()
        report.add_check(
            "Platform Detection",
            True,
            f"Detected: {config['device']} ({config['platform']})"
        )
    except Exception as e:
        report.add_check(
            "Platform Detection",
            False,
            str(e)
        )


def check_ollama_service(report: ValidationReport):
    """Check Ollama is running."""
    try:
        import urllib.request
        import json
        
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            models = data.get("models", [])
            
            report.add_check(
                "Ollama Service",
                True,
                f"{len(models)} model(s) available"
            )
    except Exception:
        report.add_check(
            "Ollama Service",
            False,
            "Not running at localhost:11434. Start: ollama serve"
        )


def check_requirements_files(report: ValidationReport):
    """Check requirements files exist."""
    root = Path(__file__).parent.parent
    
    files = {
        "requirements.txt": root / "requirements.txt",
        "requirements-cuda.txt": root / "requirements-cuda.txt",
        "requirements-metal.txt": root / "requirements-metal.txt",
    }
    
    for name, path in files.items():
        ok = path.exists()
        report.add_check(
            f"Requirements: {name}",
            ok,
            "Exists" if ok else "Missing (optional for v2.0)"
        )


def check_documentation(report: ValidationReport):
    """Check documentation exists."""
    root = Path(__file__).parent.parent
    
    docs = {
        "TRD": root / "doc" / "TRD_APPLE_SILICON_SUPPORT.md",
        "Platform Support Matrix": root / "doc" / "PLATFORM_SUPPORT_MATRIX.md",
        "Implementation Guide": root / "doc" / "APPLE_SILICON_IMPLEMENTATION_GUIDE.md",
    }
    
    for name, path in docs.items():
        ok = path.exists()
        report.add_check(
            f"Documentation: {name}",
            ok,
            "Exists" if ok else "Missing"
        )


def check_tests(report: ValidationReport):
    """Check test files updated."""
    root = Path(__file__).parent.parent
    
    test_files = [
        "tests/test_phase1_environment.py",
        "tests/test_phase2_audio.py",
    ]
    
    for test_file in test_files:
        path = root / test_file
        ok = path.exists()
        report.add_check(
            f"Test: {test_file}",
            ok,
            "Exists" if ok else "Missing"
        )


def run_basic_tests(report: ValidationReport):
    """Run basic pytest tests."""
    try:
        result = subprocess.run(
            ["pytest", "tests/test_platform_detection.py", "-v", "--tb=short"],
            cwd=Path(__file__).parent.parent,
            capture_output=True,
            timeout=30
        )
        
        ok = result.returncode == 0
        details = "All platform tests passed" if ok else "Some tests failed"
        if not ok and result.stdout:
            details += " (see test output)"
        
        report.add_check(
            "Platform Detection Tests",
            ok,
            details
        )
    except subprocess.TimeoutExpired:
        report.add_check(
            "Platform Detection Tests",
            False,
            "Timed out"
        )
    except Exception as e:
        report.add_check(
            "Platform Detection Tests",
            False,
            f"Could not run: {str(e)}"
        )


def main():
    """Run all validation checks."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Platform readiness validation")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument(
        "--platform",
        choices=["cuda", "metal", "cpu", "all"],
        default="all",
        help="Platform to check (default: all)"
    )
    
    args = parser.parse_args()
    
    print(f"\n{BOLD}{BLUE}Platform Readiness Validation{END}\n")
    
    report = ValidationReport()
    
    # Run all checks
    print("Checking Python environment...")
    check_python_version(report)
    check_pytorch_installed(report)
    
    print("Checking GPU support...")
    check_cuda_available(report)
    check_metal_available(report)
    
    print("Checking project structure...")
    check_platform_modules(report)
    check_requirements_files(report)
    check_documentation(report)
    check_tests(report)
    
    print("Checking runtime...")
    check_platform_detection_works(report)
    check_ollama_service(report)
    
    print("Running tests...")
    run_basic_tests(report)
    
    # Print report
    exit_code = report.print_report()
    
    if args.verbose:
        print(f"{BLUE}Verbose Details:{END}")
        print(f"  Python: {sys.version}")
        try:
            import torch
            print(f"  PyTorch: {torch.__version__}")
        except ImportError:
            print("  PyTorch: Not installed")
        
        try:
            import psutil
            ram = psutil.virtual_memory().total / (1024**3)
            print(f"  System RAM: {ram:.1f} GB")
        except ImportError:
            print("  System RAM: Unable to determine")
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
