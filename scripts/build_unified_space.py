"""Build a reproducible, single-Space HistAgent deployment from canonical sources.

This reads source files without importing Gradio, loading models, or downloading
data. Existing model functions remain the source of truth; only deployment
wrappers, unused UI/reference-spot code, token selection, and device placement
are adapted for the shared GPU dispatcher.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "huggingface"
OMIT_REASONING = {
    "_load_spots", "SPOT_RECORDS", "SPOT_CHOICES", "DEFAULT_SPOT",
    "answer_question", "show_evidence",
}


def _assignment_names(node: ast.AST) -> set[str]:
    targets = node.targets if isinstance(node, ast.Assign) else []
    return {part.id for target in targets for part in ast.walk(target)
            if isinstance(part, ast.Name)}


def _node_start(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno, *(item.lineno for item in decorators)])


def _remove_lines(source: str, ranges: list[tuple[int, int]]) -> str:
    excluded = {line for first, last in ranges for line in range(first, last + 1)}
    return "".join(line for number, line in enumerate(source.splitlines(keepends=True), 1)
                   if number not in excluded)


def _replace_nodes(source: str, replacements: list[tuple[ast.AST, str]]) -> str:
    # AST columns are UTF-8 byte offsets, including when a preceding comment or
    # string contains non-ASCII text.
    encoded = source.encode("utf-8")
    offsets = [0]
    for line in encoded.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    spans = [(offsets[node.lineno - 1] + node.col_offset,
              offsets[node.end_lineno - 1] + node.end_col_offset,
              replacement.encode("utf-8")) for node, replacement in replacements]
    for start, end, replacement in sorted(spans, reverse=True):
        encoded = encoded[:start] + replacement + encoded[end:]
    return encoded.decode("utf-8")


def _strip_gpu_decorators(source: str, expected: set[str]) -> str:
    ranges = []
    found = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            function = decorator.func if isinstance(decorator, ast.Call) else decorator
            if (isinstance(function, ast.Attribute) and function.attr == "GPU"
                    and isinstance(function.value, ast.Name) and function.value.id == "spaces"):
                ranges.append((decorator.lineno, decorator.end_lineno))
                found.add(node.name)
    if found != expected:
        raise ValueError(f"GPU function set changed: expected {sorted(expected)}, found {sorted(found)}")
    return _remove_lines(source, ranges)


def vision_backend(source: str) -> str:
    tree = ast.parse(source)
    blocks = [node for node in tree.body if isinstance(node, ast.With)
              and any(isinstance(item.context_expr, ast.Call)
                      and isinstance(item.context_expr.func, ast.Attribute)
                      and isinstance(item.context_expr.func.value, ast.Name)
                      and item.context_expr.func.value.id == "gr"
                      and item.context_expr.func.attr == "Blocks" for item in node.items)]
    if len(blocks) != 1:
        raise ValueError("Expected exactly one canonical inference Gradio UI")
    source = "".join(source.splitlines(keepends=True)[:blocks[0].lineno - 1])
    source = _strip_gpu_decorators(source, {"generate_ranked_readout"})
    tree = ast.parse(source)
    loaders = [node for node in tree.body if isinstance(node, ast.FunctionDef)
               and node.name == "_load_histagent"]
    if len(loaders) != 1:
        raise ValueError("Expected one HistAgent model loader")
    token_nodes = [keyword for node in ast.walk(loaders[0])
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "load_pretrained" for keyword in node.keywords
                   if keyword.arg == "token"]
    if len(token_nodes) != 1:
        raise ValueError("Expected one explicit visual-model download token")
    token = token_nodes[0]
    source = _replace_nodes(source, [(token,
        "token=MODEL_TOKEN,\n" + " " * token.col_offset
        + 'base_checkpoint_path=os.environ.get("HISTAGENT_BASE_CHECKPOINT") or None')])
    tree = ast.parse(source)
    repository = [node for node in tree.body if "MODEL_REPO" in _assignment_names(node)]
    if len(repository) != 1:
        raise ValueError("Expected one visual-model repository declaration")
    lines = source.splitlines(keepends=True)
    lines.insert(repository[0].end_lineno,
                 'MODEL_TOKEN = os.environ.get("HISTAGENT_VISION_TOKEN") or os.environ.get("HF_TOKEN")\n')
    return "".join(lines).rstrip() + "\n"


def reasoning_backend(source: str) -> str:
    tree = ast.parse(source)
    css = [node for node in tree.body if "CSS" in _assignment_names(node)]
    if len(css) != 1:
        raise ValueError("Expected exactly one canonical reasoning CSS/UI boundary")
    source = "".join(source.splitlines(keepends=True)[:css[0].lineno - 1])
    ranges = []
    removed = set()
    for node in ast.parse(source).body:
        names = ({node.name} if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 else _assignment_names(node))
        if names & OMIT_REASONING:
            if not names <= OMIT_REASONING:
                raise ValueError(f"Cannot partially omit combined assignment: {sorted(names)}")
            removed.update(names)
            ranges.append((_node_start(node), node.end_lineno))
    if removed != OMIT_REASONING:
        raise ValueError(f"Reference-spot source layout changed; missing {sorted(OMIT_REASONING - removed)}")
    source = _remove_lines(source, ranges)
    source = _strip_gpu_decorators(source, {"answer_atlas_question", "retrieve_atlas"})
    replacements = []
    loaders = set()
    for node in ast.parse(source).body:
        if not isinstance(node, ast.FunctionDef) or node.name not in {"_load_qwen", "_load_embedder"}:
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            for keyword in call.keywords:
                if keyword.arg == "device_map":
                    if not isinstance(keyword.value, ast.Constant) or keyword.value.value != "auto":
                        raise ValueError(f"Unexpected canonical device map in {node.name}")
                    replacements.append((keyword.value, '"cuda"'))
                    loaders.add(node.name)
    if loaders != {"_load_qwen", "_load_embedder"} or len(replacements) != 2:
        raise ValueError("Expected the two canonical reasoning model device maps")
    return _replace_nodes(source, replacements).rstrip() + "\n"


def collect_bundle(root: Path = ROOT) -> tuple[dict[str, bytes], dict[str, Any]]:
    deploy = root / "deploy" / "huggingface"
    bundle: dict[str, bytes] = {}
    sources: dict[str, str] = {}
    files: dict[str, dict[str, str]] = {}

    def add(path: Path, destination: str, transform=None, description: str = "copy") -> None:
        if path.is_symlink():
            raise ValueError(f"Source symlinks are not bundled: {path}")
        original = path.read_bytes()
        source_path = path.relative_to(root).as_posix()
        sources[source_path] = hashlib.sha256(original).hexdigest()
        output = transform(original.decode("utf-8")).encode("utf-8") if transform else original
        if destination.endswith(".py"):
            compile(output, destination, "exec")
        bundle[destination] = output
        files[destination] = {"source": source_path, "transform": description,
                              "sha256": hashlib.sha256(output).hexdigest()}

    for filename in ("app.py", "local_backend.py", "requirements.txt", "README.md"):
        add(deploy / "histagent-unified" / filename, filename)
    add(deploy / "histagent-api" / "app.py", "gateway.py")
    add(deploy / "histagent-inference" / "app.py", "vision_backend.py", vision_backend,
        "Remove UI and spaces.GPU decorator; select vision token and optional local base checkpoint")
    add(deploy / "histagent-agent" / "app.py", "reasoning_backend.py", reasoning_backend,
        "Remove UI/reference-spot features and spaces.GPU decorators; set model device_map to cuda")
    package = deploy / "histagent-inference" / "histagent"
    for path in sorted(package.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"} or path.name == ".DS_Store":
            continue
        add(path, "histagent/" + path.relative_to(package).as_posix())
    builder = root / "scripts" / "build_unified_space.py"
    sources[builder.relative_to(root).as_posix()] = hashlib.sha256(builder.read_bytes()).hexdigest()
    manifest = {"schema_version": 1, "builder": "scripts/build_unified_space.py",
                "sources": dict(sorted(sources.items())), "files": dict(sorted(files.items()))}
    bundle["bundle-manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return bundle, manifest


def build(output: Path, *, force: bool = False, root: Path = ROOT) -> dict[str, Any]:
    if output.is_symlink():
        raise ValueError("Output must be a directory, not a symlink")
    output = output.resolve()
    root = root.resolve()
    if output.exists() and not output.is_dir():
        raise ValueError("Output already exists and is not a directory")
    if output.exists() and any(output.iterdir()) and not force:
        raise ValueError("Output is not empty; choose a new directory or pass --force explicitly")
    bundle, manifest = collect_bundle(root)
    for relative in manifest["sources"]:
        source = (root / relative).resolve()
        if output == source or output in source.parents:
            raise ValueError("Output would overwrite canonical source files")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-build-", dir=output.parent))
    try:
        for relative, content in bundle.items():
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        if output.exists():
            shutil.rmtree(output)
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Destination deployment directory")
    parser.add_argument("--force", action="store_true", help="Replace an existing nonempty output directory")
    args = parser.parse_args()
    try:
        manifest = build(args.output, force=args.force)
    except (OSError, ValueError, SyntaxError) as error:
        parser.exit(1, f"Cannot build unified Space: {error}\n")
    print(json.dumps({"output": str(args.output.resolve()), "files": len(manifest["files"]) + 1,
                      "manifest": "bundle-manifest.json"}))


if __name__ == "__main__":
    main()
