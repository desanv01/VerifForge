"""Fixed recorded native import. Default preparation opens no database."""
from veriforge.system_investigation.native_tool_import import main


if __name__ == "__main__":
    raise SystemExit(main())
