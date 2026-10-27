"""Deploy the document chat as a Databricks App.

    python procurement_rag/databricks_app/deploy.py --profile free

1. Stages the app (app.py, app.yaml, requirements.txt and proc_lib.py, a copy of notebooks/20_proc_lib.py) and uploads
   it next to the notebooks in the workspace.
2. Creates the app `proc-lab-docs-chat` with its resources (SQL warehouse, embedding and chat model endpoints) if it
   does not exist yet.
3. Grants the app's service principal read access to the catalog: the country table, the passages and the vector index.
4. Deploys and prints the app URL.
"""
import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
APP = "proc-lab-docs-chat"


def cli(profile, *args, check=True):
    r = subprocess.run(["databricks", "-p", profile, *args, "-o", "json"], capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode:
        raise SystemExit(r.stderr.strip()[-1500:])
    if r.returncode or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:          # commands such as import-dir print one JSON line per file
        return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="free")
    ap.add_argument("--warehouse", help="SQL warehouse id (default: the first one)")
    ap.add_argument("--catalog", default="proc_demo")
    a = ap.parse_args()

    me = cli(a.profile, "current-user", "me")["userName"]
    target = f"/Workspace/Users/{me}/procurement-intelligence/app"
    stage = Path(tempfile.mkdtemp())
    for f in ("app.py", "app.yaml", "requirements.txt"):
        shutil.copy(HERE / f, stage / f)
    # without the "# Databricks notebook source" header the upload stores it as a plain file the app can import
    lib_src = (ROOT / "notebooks" / "20_proc_lib.py").read_text(encoding="utf-8")
    (stage / "proc_lib.py").write_text(lib_src.replace("# Databricks notebook source\n", "", 1), encoding="utf-8")
    cli(a.profile, "workspace", "delete", f"{target}/proc_lib", check=False)        # an earlier upload stored it as a notebook
    cli(a.profile, "workspace", "import-dir", str(stage), target, "--overwrite")
    print("uploaded to", target)

    warehouse = a.warehouse or cli(a.profile, "warehouses", "list")[0]["id"]
    if not cli(a.profile, "apps", "get", APP, check=False):
        spec = {"name": APP, "description": "Lab and diagnostic equipment procurement: questions answered from a country's "
                                             "World Bank project documents, with sources",
                "resources": [
                    {"name": "sql-warehouse", "sql_warehouse": {"id": warehouse, "permission": "CAN_USE"}},
                    {"name": "embeddings", "serving_endpoint": {"name": "databricks-gte-large-en", "permission": "CAN_QUERY"}},
                    {"name": "chat-model", "serving_endpoint": {"name": "databricks-meta-llama-3-3-70b-instruct",
                                                                "permission": "CAN_QUERY"}}]}
        cli(a.profile, "apps", "create", "--json", json.dumps(spec), "--timeout", "30m")
        print("created app", APP)
    app = cli(a.profile, "apps", "get", APP)
    sp = app["service_principal_client_id"]
    c = a.catalog
    for stmt in (f"GRANT USE CATALOG ON CATALOG {c} TO `{sp}`",
                 f"GRANT USE SCHEMA ON SCHEMA {c}.gold TO `{sp}`", f"GRANT USE SCHEMA ON SCHEMA {c}.ops TO `{sp}`",
                 f"GRANT USE SCHEMA ON SCHEMA {c}.ai TO `{sp}`",
                 f"GRANT SELECT ON TABLE {c}.gold.rag_chunks TO `{sp}`", f"GRANT SELECT ON TABLE {c}.ops.rag_countries TO `{sp}`",
                 f"GRANT SELECT ON TABLE {c}.ai.rag_chunks_index TO `{sp}`"):
        r = cli(a.profile, "api", "post", "/api/2.0/sql/statements",
                "--json", json.dumps({"warehouse_id": warehouse, "statement": stmt, "wait_timeout": "50s"}))
        print(r["status"]["state"], stmt)
    cli(a.profile, "apps", "deploy", APP, "--source-code-path", target, "--timeout", "30m")
    print("deployed:", cli(a.profile, "apps", "get", APP).get("url"))


if __name__ == "__main__":
    main()
