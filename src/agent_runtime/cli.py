"""CLI entrypoint for Agent Runtime."""

import argparse
import sys

from agent_runtime import __version__
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

    args = parser.parse_args()

    if args.command == "serve":
        run_serve(args)
    elif args.command == "env":
        run_env(args)
    elif args.command == "pairing":
        run_pairing(args)
    else:
        parser.print_help()
        sys.exit(1)


def run_serve(args: argparse.Namespace) -> None:
    """Run the server."""
    import uvicorn

    from agent_runtime.config import settings as cfg

    setup_logging(structured=False)

    # Update settings from args
    if args.no_pairing:
        cfg.require_pairing = False

    print(f"Starting Agent Runtime on http://{args.host}:{args.port}")
    print(f"Pairing required: {cfg.require_pairing}")
    print(f"Debug mode: {args.debug}")
    print()

    uvicorn.run(
        "agent_runtime.server:app",
        host=args.host,
        port=args.port,
        reload=args.debug,
    )


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


if __name__ == "__main__":
    main()
