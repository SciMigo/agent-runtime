"""CLI entrypoint for Agent Runtime."""

import argparse
import sys

from agent_runtime import __version__
from agent_runtime.auth import SCOPE_DESCRIPTIONS, SCOPES
from agent_runtime.config import settings
from agent_runtime.observability import setup_logging


def main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="agent-runtime",
        description="Lightweight execution engine for AI agents",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"agent-runtime {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # serve command
    serve_parser = subparsers.add_parser("serve", help="Start the runtime server")
    serve_parser.add_argument(
        "--host",
        default=settings.host,
        help=f"Host to bind to (default: {settings.host})",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=settings.port,
        help=f"Port to bind to (default: {settings.port})",
    )
    serve_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with auto-reload",
    )
    serve_parser.add_argument(
        "--no-pairing",
        action="store_true",
        help="Disable pairing requirement (for development)",
    )
    serve_parser.add_argument(
        "--https-port",
        type=int,
        default=settings.https_port,
        help=f"HTTPS port, used once 'tls setup' has run (default: {settings.https_port})",
    )
    serve_parser.add_argument(
        "--no-https",
        action="store_true",
        help="Serve HTTP only, even when a TLS certificate exists",
    )

    # tls commands
    tls_parser = subparsers.add_parser(
        "tls", help="Manage the loopback HTTPS certificate (needed for Safari)"
    )
    tls_subparsers = tls_parser.add_subparsers(dest="tls_command")
    tls_setup = tls_subparsers.add_parser(
        "setup", help="Create the certificate and, on macOS, trust it in the login keychain"
    )
    tls_setup.add_argument("--force", action="store_true", help="Replace an existing certificate")
    tls_setup.add_argument(
        "--no-trust", action="store_true", help="Create the certificate without trusting it"
    )
    tls_subparsers.add_parser("status", help="Show the certificate, its expiry and trust")
    tls_subparsers.add_parser("remove", help="Untrust and delete the certificate")

    # env commands
    env_parser = subparsers.add_parser("env", help="Manage environments")
    env_subparsers = env_parser.add_subparsers(dest="env_command")

    env_subparsers.add_parser("list", help="List environments")

    env_create = env_subparsers.add_parser("create", help="Create an environment")
    env_create.add_argument("lab_id", help="Lab identifier")
    env_create.add_argument("--python", help="Python version to use")

    env_delete = env_subparsers.add_parser("delete", help="Delete an environment")
    env_delete.add_argument("lab_id", help="Lab identifier")

    # pairing commands
    pairing_parser = subparsers.add_parser("pairing", help="Manage pairing")
    pairing_subparsers = pairing_parser.add_subparsers(dest="pairing_command")

    pairing_subparsers.add_parser("list", help="List paired origins")
    pairing_revoke = pairing_subparsers.add_parser("revoke", help="Revoke a paired origin")
    pairing_revoke.add_argument("origin", help="Origin to revoke")

    # token commands: bearer tokens for local processes, which have no origin to pair
    token_parser = subparsers.add_parser(
        "token", help="Manage tokens for local clients, such as an MCP server"
    )
    token_subparsers = token_parser.add_subparsers(dest="token_command")

    token_create = token_subparsers.add_parser("create", help="Issue a token to a local client")
    token_create.add_argument("name", help="What to call this client, e.g. 'Claude Desktop'")
    token_create.add_argument(
        "--scope",
        choices=SCOPES,
        default="actions",
        help="What the client may do (default: actions, which cannot run arbitrary code)",
    )
    token_subparsers.add_parser("list", help="List local clients")
    token_revoke = token_subparsers.add_parser("revoke", help="Revoke a local client's token")
    token_revoke.add_argument("name", help="The client to revoke")

    # mcp command
    mcp_parser = subparsers.add_parser(
        "mcp", help="Run the MCP server on stdio, for an agent client to connect to"
    )
    mcp_parser.add_argument(
        "--url",
        default=None,
        help="The runtime to talk to (default: $AGENT_RUNTIME_URL, else the local runtime)",
    )
    mcp_parser.add_argument(
        "--token",
        default=None,
        help="Local client token (default: $AGENT_RUNTIME_TOKEN)",
    )

    args = parser.parse_args()

    if args.command == "serve":
        run_serve(args)
    elif args.command == "env":
        run_env(args)
    elif args.command == "pairing":
        run_pairing(args)
    elif args.command == "token":
        run_token(args)
    elif args.command == "mcp":
        run_mcp(args)
    elif args.command == "tls":
        run_tls(args)
    else:
        parser.print_help()
        sys.exit(1)


def run_serve(args: argparse.Namespace) -> None:
    """Run the server: HTTP, plus HTTPS on loopback once `tls setup` has created a certificate."""
    import asyncio

    import uvicorn

    from agent_runtime import tls
    from agent_runtime.config import settings as cfg

    setup_logging(structured=False)

    # Update settings from args
    if args.no_pairing:
        cfg.require_pairing = False

    cert = None if args.no_https or args.debug else tls.load(cfg.tls_cert_file, cfg.tls_key_file)
    https_note = ""
    if cert and _port_in_use(args.host, args.https_port):
        https_note = f"HTTPS: port {args.https_port} is in use, serving HTTP only"
        cert = None
    elif cert and cert.expires_within(0):
        https_note = "HTTPS: the certificate has expired; run 'agent-runtime tls setup'"
        cert = None
    elif cert and cert.expires_within(tls.RENEW_BEFORE_DAYS):
        https_note = "HTTPS: the certificate expires soon; 'agent-runtime tls setup' renews it"
    elif not cert and not args.no_https and tls.trust_supported():
        https_note = "HTTPS: off. Safari needs it; run 'agent-runtime tls setup' once to enable it"

    print(f"Starting Agent Runtime on http://{args.host}:{args.port}")
    if cert:
        print(f"                       and https://{args.host}:{args.https_port}")
    if https_note:
        print(https_note)
    print(f"Pairing required: {cfg.require_pairing}")
    print(f"Debug mode: {args.debug}")
    print()

    if args.debug:
        # Auto-reload needs uvicorn's own runner (an import string, one listener).
        uvicorn.run("agent_runtime.server:app", host=args.host, port=args.port, reload=True)
        return

    from agent_runtime.server import app
    from agent_runtime.serving import build_listeners, serve_all

    listeners = build_listeners(
        app,
        args.host,
        args.port,
        https_port=args.https_port if cert else None,
        certfile=str(cert.cert_path) if cert else None,
        keyfile=str(cert.key_path) if cert else None,
    )
    asyncio.run(serve_all(listeners))


def _port_in_use(host: str, port: int) -> bool:
    import socket

    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def run_tls(args: argparse.Namespace) -> None:
    """Handle tls commands."""
    from agent_runtime import tls
    from agent_runtime.config import settings as cfg

    cert_path, key_path = cfg.tls_cert_file, cfg.tls_key_file

    if args.tls_command == "setup":
        info, created = tls.ensure(cert_path, key_path, force=args.force)
        print(f"{'Created' if created else 'Using'} certificate {info.cert_path}")
        print(f"  valid for 127.0.0.1, ::1 and localhost until {info.not_after:%Y-%m-%d}")
        print(f"  SHA-256 {info.sha256}")
        if args.no_trust:
            print("Not trusted (--no-trust).")
        elif not tls.trust_supported():
            print(
                "No trust step needed on this platform: Chrome and Firefox reach "
                f"http://127.0.0.1:{cfg.port} directly. Safari, which needs HTTPS, is macOS only."
            )
        elif not created and tls.is_trusted(info):
            print("Already trusted in the login keychain.")
        else:
            print("Adding it to the login keychain, trusted for SSL only.")
            print("macOS asks for your password.")
            try:
                tls.trust(info)
            except tls.TLSError as error:
                print(f"Could not trust the certificate: {error}")
                sys.exit(1)
            print("Trusted.")
        print(
            f"Restart 'agent-runtime serve'; it also listens on https://127.0.0.1:{cfg.https_port}."
        )

    elif args.tls_command == "status":
        found = tls.load(cert_path, key_path)
        if found is None:
            print("No certificate. Run 'agent-runtime tls setup' to enable HTTPS for Safari.")
            return
        trusted = tls.is_trusted(found)
        state = {True: "trusted", False: "NOT trusted", None: "trust not checked on this platform"}
        print(f"Certificate: {found.cert_path}")
        renew = " (renew soon)" if found.expires_within(tls.RENEW_BEFORE_DAYS) else ""
        print(f"  expires {found.not_after:%Y-%m-%d}{renew}")
        print(f"  SHA-256 {found.sha256}")
        print(f"  {state[trusted]}")

    elif args.tls_command == "remove":
        found = tls.load(cert_path, key_path)
        if found is None:
            print("No certificate to remove.")
            return
        tls.untrust(found)
        tls.remove_files(found)
        print("Removed the certificate and its keychain trust. The runtime serves HTTP only.")

    else:
        print("Usage: agent-runtime tls [setup|status|remove]")
        sys.exit(1)


def run_env(args: argparse.Namespace) -> None:
    """Handle env commands."""
    from agent_runtime.envs import env_manager

    if args.env_command == "list":
        envs = env_manager.list_envs()
        if not envs:
            print("No environments found")
            return

        print(f"Found {len(envs)} environment(s):\n")
        for env in envs:
            print(f"  Lab: {env['lab_id']}")
            print(f"    Path: {env['path']}")
            print(f"    Kernel: {env['kernel_name']}")
            print()

    elif args.env_command == "create":
        print(f"Creating environment for lab {args.lab_id}...")
        path = env_manager.create_env(args.lab_id, python_version=args.python)
        print(f"Environment created at: {path}")

        print("Installing kernel spec...")
        kernel_name = env_manager.install_kernel_spec(args.lab_id)
        print(f"Kernel installed: {kernel_name}")

    elif args.env_command == "delete":
        success = env_manager.delete_env(args.lab_id)
        if success:
            print(f"Environment for lab {args.lab_id} deleted")
        else:
            print(f"No environment found for lab {args.lab_id}")
            sys.exit(1)

    else:
        print("Usage: agent-runtime env [list|create|delete]")
        sys.exit(1)


def run_pairing(args: argparse.Namespace) -> None:
    """Handle pairing commands."""
    from agent_runtime.auth import pairing_manager

    if args.pairing_command == "list":
        origins = pairing_manager.get_allowed_origins()
        if not origins:
            print("No paired origins")
            return

        print("Paired origins (loopback origins are trusted automatically):")
        for origin in origins:
            print(f"  - {origin}")

    elif args.pairing_command == "revoke":
        success = pairing_manager.revoke_origin(args.origin)
        if success:
            print(f"Revoked pairing for: {args.origin}")
        else:
            print(f"Origin not found: {args.origin}")
            sys.exit(1)

    else:
        print("Usage: agent-runtime pairing [list|revoke]")
        sys.exit(1)


def run_token(args: argparse.Namespace) -> None:
    """Handle token commands: bearer tokens for local processes."""
    from agent_runtime.auth import create_local_token
    from agent_runtime.local_tokens import local_tokens

    if args.token_command == "create":
        existed = any(client.name == args.name for client in local_tokens.list_clients())
        try:
            token = create_local_token(args.name, args.scope)
        except ValueError as error:
            print(error)
            sys.exit(1)

        if existed:
            print(f"Replaced the token for {args.name!r}; the old one no longer works.")
        print(f"Token for {args.name!r}, scope {args.scope}:")
        print()
        print(f"  {token}")
        print()
        print("This is the only time it is shown. It grants the right to")
        print(f"{SCOPE_DESCRIPTIONS[args.scope]}.")
        print()
        print("For an MCP client, put it in that client's own configuration:")
        print()
        print('  "agent-runtime": {')
        print('    "command": "agent-runtime",')
        print('    "args": ["mcp"],')
        print(f'    "env": {{"AGENT_RUNTIME_TOKEN": "{token}"}}')
        print("  }")
        if args.scope != "code":
            print()
            print("Note: running Python needs --scope code. This token can only start the")
            print("named actions of labs you approve.")

    elif args.token_command == "list":
        clients = local_tokens.list_clients()
        if not clients:
            print("No local clients. Issue a token with 'agent-runtime token create <name>'.")
            return

        print("Local clients:")
        for client in clients:
            created = local_tokens.created_at(client.name)
            when = f", created {created[:10]}" if created else ""
            print(f"  - {client.name} (scope {client.scope}{when})")

    elif args.token_command == "revoke":
        if local_tokens.revoke(args.name):
            print(f"Revoked the token for {args.name!r}")
        else:
            print(f"No local client named {args.name!r}")
            sys.exit(1)

    else:
        print("Usage: agent-runtime token [create|list|revoke]")
        sys.exit(1)


def run_mcp(args: argparse.Namespace) -> None:
    """Run the MCP server on stdio.

    Started by an MCP client, not usually by hand: stdin and stdout carry the protocol.
    """
    import os

    try:
        from agent_runtime.mcp.server import serve
    except ImportError as error:
        print(
            f"The MCP server needs the 'mcp' package ({error}). Install it with:\n"
            "  uv pip install 'agent-runtime[mcp]'",
            file=sys.stderr,
        )
        sys.exit(1)

    serve(base_url=args.url, token=args.token or os.environ.get("AGENT_RUNTIME_TOKEN"))


if __name__ == "__main__":
    main()
