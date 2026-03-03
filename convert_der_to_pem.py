"""
convert_der_to_pem.py
=====================
Converts a Burp Suite CA certificate from DER format to PEM format.

Background
----------
Burp Suite Community Edition exports its CA certificate in DER (binary) format only.
Python's ssl module requires PEM (Base64-encoded) format for explicit CA loading.
Windows CMD: even after installing the DER certificate into the Windows Trusted Root
store, Python's urllib does NOT use the Windows certificate store — it uses its own
bundle. The only reliable method is loading the PEM file explicitly via
ssl.SSLContext.load_verify_locations().

This script performs the conversion without any external dependencies.

Usage
-----
  1. In Burp Suite: Proxy → Proxy Settings → Import/Export CA Certificate
     → Export Certificate → DER format → save as burp-ca.der
  2. Place burp-ca.der in the project root (same directory as this script)
  3. Run: python convert_der_to_pem.py
  4. burp-ca.pem is created in the project root
  5. Set environment variable: set BURP_CA_PEM_PATH=D:\\path\\to\\burp-ca.pem

Security notes
--------------
- Never commit burp-ca.der or burp-ca.pem to version control (.gitignore handles this)
- The PEM file is only valid while the corresponding Burp instance is running
- Regenerating the Burp CA (Proxy → Settings → Regenerate CA) invalidates this file

Troubleshooting — SSL errors encountered during development
-----------------------------------------------------------
Error 1: CERTIFICATE_VERIFY_FAILED: Missing Authority Key Identifier
  Cause:  Burp Community CA lacks the AKI extension that Python 3.10+ enforces
  Fix:    Load PEM explicitly via ctx.load_verify_locations() rather than relying
          on the system certificate store

Error 2: CERTIFICATE_VERIFY_FAILED: CA cert does not include key usage extension
  Cause:  Python 3.13 enforces stricter X.509 validation (VERIFY_X509_STRICT)
  Fix:    Same — explicit PEM load via ssl context; regenerate CA if it persists

Error 3: WinError 10061 — No connection could be made (target machine actively refused)
  Cause:  Burp Suite had Intercept ON, blocking all traffic
  Fix:    Proxy → Intercept → turn Intercept OFF for passive observation

Error 4: Environment variable echo shows variable name not value (e.g., "HTTP_PROXY")
  Cause:  Windows CMD requires %VARNAME% syntax, not $VARNAME
  Fix:    echo %HTTP_PROXY%  (not echo $HTTP_PROXY)

Author: Robert Jurkevich
https://www.linkedin.com/in/rjurkevich/
"""

import base64
import sys
from pathlib import Path


def convert_der_to_pem(
    der_path: str = "burp-ca.der",
    pem_path: str = "burp-ca.pem",
) -> bool:
    """
    Read a DER-encoded certificate and write the equivalent PEM file.

    PEM format wraps the base64-encoded DER content between standard
    certificate header/footer lines that ssl.load_verify_locations() expects.

    Args:
        der_path: Path to input DER file.
        pem_path: Path to output PEM file.

    Returns:
        True on success, False on failure.
    """
    der_file = Path(der_path)
    pem_file = Path(pem_path)

    if not der_file.exists():
        print(f"  [ERROR] DER file not found: {der_file.resolve()}")
        print("  Export from Burp: Proxy → Proxy Settings → Import/Export CA Certificate")
        print("  → Export Certificate → DER format → save as burp-ca.der in project root")
        return False

    try:
        der_data = der_file.read_bytes()
    except OSError as e:
        print(f"  [ERROR] Cannot read {der_file}: {e}")
        return False

    # Base64-encode the DER bytes and wrap in PEM headers.
    # Strict line wrapping at 64 characters matches RFC 7468 / openssl convention.
    b64 = base64.b64encode(der_data).decode("ascii")
    lines = [b64[i:i+64] for i in range(0, len(b64), 64)]
    pem_content = (
        "-----BEGIN CERTIFICATE-----\n"
        + "\n".join(lines)
        + "\n-----END CERTIFICATE-----\n"
    )

    try:
        pem_file.write_text(pem_content, encoding="ascii")
    except OSError as e:
        print(f"  [ERROR] Cannot write {pem_file}: {e}")
        return False

    print(f"  [OK] Converted {der_file.name} → {pem_file.name}")
    print(f"       DER size:  {len(der_data):,} bytes")
    print(f"       PEM size:  {pem_file.stat().st_size:,} bytes")
    print()
    print("  Next steps:")
    print(f"  1. Set BURP_CA_PEM_PATH={pem_file.resolve()}")
    print("  2. Set BURP_PROXY_ENABLED=true")
    print("  3. Ensure Burp proxy listener is active on 127.0.0.1:8080")
    print("  4. In Burp: Proxy → Intercept → Intercept OFF")
    print("  5. Run: python main.py")
    return True


def verify_pem(pem_path: str = "burp-ca.pem") -> bool:
    """
    Verify the generated PEM file can be loaded by Python's ssl module.
    If this passes, the file is valid for use in ssl.SSLContext.load_verify_locations().
    """
    import ssl
    pem_file = Path(pem_path)
    if not pem_file.exists():
        print(f"  [SKIP] PEM file not found for verification: {pem_file}")
        return False
    try:
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cafile=str(pem_file))
        print(f"  [OK] SSL verification passed — {pem_file.name} is valid")
        return True
    except ssl.SSLError as e:
        print(f"  [FAIL] SSL verification failed: {e}")
        print("         Try regenerating the Burp CA: Proxy → Settings → Regenerate CA")
        return False


if __name__ == "__main__":
    print()
    print("Burp Suite CA Certificate — DER to PEM Converter")
    print("=" * 50)
    print()

    der_path = sys.argv[1] if len(sys.argv) > 1 else "burp-ca.der"
    pem_path = sys.argv[2] if len(sys.argv) > 2 else "burp-ca.pem"

    success = convert_der_to_pem(der_path, pem_path)

    if success:
        print()
        print("Verifying PEM with Python ssl module...")
        verify_pem(pem_path)
