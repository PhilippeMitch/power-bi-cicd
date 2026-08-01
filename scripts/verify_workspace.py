"""Diagnose 'workspace not found' errors before running fabric-cicd.

Checks, in order:
  1. --workspace-id looks like a GUID (catches stray whitespace/quotes/newlines
     pasted into the GitHub secret, or a report/dataset ID used by mistake).
  2. The service principal can authenticate and obtain a Fabric API token.
  3. The service principal can see that specific workspace via the Fabric API.
  4. If not, lists every workspace the service principal CAN see, so it's
     obvious whether the SP just isn't a member of the target workspace yet
     (README section 2.3) vs. the ID itself being wrong.

Never prints the workspace ID or token in full - only enough to spot
whitespace/formatting issues without leaking anything sensitive in CI logs.
"""

import argparse
import os
import re
import sys

import requests
from azure.identity import ClientSecretCredential

FABRIC_API = "https://api.fabric.microsoft.com/v1"
GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def mask(value: str) -> str:
    return f"(len={len(value)}) ...{value[-4:]}" if len(value) >= 4 else f"(len={len(value)})"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a Fabric workspace ID / SP access before deploying")
    parser.add_argument("--workspace-id", required=True, help="Target Fabric workspace ID (GUID)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_id = args.workspace_id
    workspace_id = raw_id.strip()

    print(f"Raw --workspace-id as received: {mask(raw_id)}")
    if raw_id != workspace_id:
        print("::warning::--workspace-id has leading/trailing whitespace - check the GitHub secret value for a stray space or newline.")

    if not GUID_RE.match(workspace_id):
        sys.exit(
            f"::error::'{mask(workspace_id)}' does not look like a GUID. "
            "Copy the ID from the workspace settings URL (.../groups/<this-guid>/...), "
            "not a report/dataset/app URL."
        )

    try:
        tenant_id = os.environ["AZURE_TENANT_ID"]
        client_id = os.environ["AZURE_CLIENT_ID"]
        client_secret = os.environ["AZURE_CLIENT_SECRET"]
    except KeyError as exc:
        sys.exit(f"Missing required environment variable: {exc}")

    credential = ClientSecretCredential(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)

    try:
        token = credential.get_token("https://api.fabric.microsoft.com/.default").token
    except Exception as exc:  # noqa: BLE001 - want to surface any auth failure as a clear CI error
        sys.exit(f"::error::Service principal failed to authenticate: {exc}")

    print("Service principal authenticated successfully.")
    headers = {"Authorization": f"Bearer {token}"}

    resp = requests.get(f"{FABRIC_API}/workspaces/{workspace_id}", headers=headers, timeout=30)
    if resp.status_code == 200:
        ws = resp.json()
        print(f"OK: service principal can see workspace '{ws.get('displayName')}' (type={ws.get('type')}).")
        return

    print(f"::warning::GET /workspaces/{{id}} returned {resp.status_code}: {resp.text[:300]}")
    print("Listing workspaces visible to this service principal (checks membership vs. tenant setting propagation):")

    list_resp = requests.get(f"{FABRIC_API}/workspaces", headers=headers, timeout=30)
    if not list_resp.ok:
        sys.exit(
            f"::error::Could not list workspaces either ({list_resp.status_code}: {list_resp.text[:300]}). "
            "The 'Service principals can use Fabric APIs' tenant setting is likely not enabled/propagated yet, "
            "or the security group scoping it doesn't include this SP (README section 2.2)."
        )

    workspaces = list_resp.json().get("value", [])
    if not workspaces:
        sys.exit(
            "::error::Service principal authenticates fine but is not a member of ANY workspace. "
            "Add it to the DEV workspace with Member/Contributor access (README section 2.3)."
        )

    print(f"Service principal can see {len(workspaces)} workspace(s):")
    for ws in workspaces:
        print(f"  - {ws.get('displayName')}  id={ws.get('id')}")
    sys.exit(
        f"::error::Target workspace {mask(workspace_id)} is not in the list above. "
        "Either FABRIC_WORKSPACE_ID points at the wrong workspace, or the SP hasn't been added to it yet."
    )


if __name__ == "__main__":
    main()
