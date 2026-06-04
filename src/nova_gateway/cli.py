from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="nova")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="启动 Nova 本地 Web 网关")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8765, type=int)

    doctor = subparsers.add_parser("doctor", help="检查 Nova 运行环境")
    doctor.set_defaults(command="doctor")

    args = parser.parse_args()

    if args.command == "doctor":
        print("Nova doctor: Python 后端入口可用。")
        return

    if args.command in {"serve", None}:
        uvicorn.run("nova_gateway.main:app", host=args.host, port=args.port, reload=False)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
