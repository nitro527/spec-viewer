"""``specview-annotate build|serve``."""
from __future__ import annotations

import argparse
from pathlib import Path

from specview.store import Library

from .pack import build_pack, dumps


def cmd_build(args):
    lib = Library(Path(args.library))
    versions = args.version or [v["version"] for v in lib.versions(args.spec)][1:]
    model = None
    if args.llm == "claude":
        from .llm import ClaudeIntentModel
        model = ClaudeIntentModel(language=args.language)
    elif args.llm:
        from .llm_openai import OpenAICompatIntentModel
        model = OpenAICompatIntentModel.from_preset(args.llm, model=args.llm_model, base_url=args.llm_base_url,
                                                    language=args.language)
    for v in versions:
        pack = build_pack(lib, args.spec, v, model, limit=args.limit,
                          progress=lambda i, n: print(f"\r  {args.spec} v{v}: {i}/{n}", end="", flush=True))
        out = Path(args.out) / args.spec / f"{v}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(dumps(pack), encoding="utf-8")
        print(f"\n  -> {out} ({len(pack['changes'])} changes)")


def cmd_serve(args):
    import uvicorn
    from .app import create_app
    uvicorn.run(create_app(Path(args.out)), host=args.host, port=args.port)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="specview-annotate")
    ap.add_argument("--out", default="packs", help="pack directory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build", help="build packs from a specview library")
    p.add_argument("spec")
    p.add_argument("--library", required=True, help="path to library.sqlite (CR covers + 38.822 fetched)")
    p.add_argument("--version", action="append")
    p.add_argument("--llm", help="add change-intent summaries: claude | gemini | vllm | ollama | openai "
                                 "(any OpenAI-compatible server with --llm-base-url)")
    p.add_argument("--llm-base-url", help="OpenAI-compatible base URL, e.g. http://llm.corp:8000/v1")
    p.add_argument("--llm-model", help="model name on that server, e.g. glm-5.3")
    p.add_argument("--limit", type=int, help="only the first N changes per version (for trying a model)")
    p.add_argument("--language", default="Korean")
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("serve", help="serve the pack directory")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8800)
    p.set_defaults(func=cmd_serve)
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
