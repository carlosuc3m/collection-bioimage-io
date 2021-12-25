import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from pprint import pprint

import typer
from boltons.iterutils import remap
from ruamel.yaml import YAML

yaml = YAML(typ="safe")


def set_gh_actions_output(name: str, output: str):
    """set output of a github actions workflow step calling this script"""
    # escape special characters when setting github actions step output
    output = output.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::set-output name={name}::{output}")


def main() -> int:
    collection_path = Path("collection")
    rdf = yaml.load(Path("collection_rdf_template.yaml"))
    rdf['attachments'] = rdf.get('attachments', {})
    attachments = rdf['attachments']

    subprocess.run(["git", "fetch"])
    remote_branch_proc = subprocess.run(["git", "branch", "-r"], capture_output=True, text=True)
    remote_branches = remote_branch_proc.stdout.split()
    print("found remote branches:")
    pprint(remote_branches)

    gh_pages = Path("dist/gh-pages")
    subprocess.run(["git", "worktree", "add", str(gh_pages), f"gh-pages"])
    gh_pages_previews = Path("dist/gh-pages-previews")
    gh_pages_update = Path("dist/gh-pages-update")
    gh_pages_update.mkdir(parents=True, exist_ok=True)

    processed_gh_pages_previews = []

    n_accepted = {}
    n_accepted_versions = {}
    known_resources = list(collection_path.glob("**/resource.yaml"))
    for r_path in known_resources:
        r = yaml.load(r_path)
        if r["status"] != "accepted":
            continue

        # deploy from preview if it exists
        preview_branch = f"gh-pages-auto-update-{r['resource_id']}"
        from_preview = f"origin/{preview_branch}" in remote_branches
        # checkout preview separately
        ghp_prev = gh_pages_previews / preview_branch
        if from_preview:
            print(f"checkout {preview_branch} at {ghp_prev}")
            subprocess.run(["git", "worktree", "add", str(ghp_prev), f"{preview_branch}"])

        latest_version = None
        for v in r["versions"]:
            if v["status"] != "accepted":
                continue

            version_sub_path = Path("resources") / v["version_id"]
            if from_preview:
                v_path = ghp_prev / version_sub_path / "rdf.yaml"
                # move gh-pages preview content to gh-pages update
                if v_path.exists():
                    processed_gh_pages_previews.append(preview_branch)
                    ghp_up = gh_pages_update / version_sub_path
                    ghp_up.mkdir(parents=True)
                    shutil.copytree(
                        str(ghp_prev / version_sub_path), str(ghp_up), copy_function=shutil.move, dirs_exist_ok=True
                    )
                    print("update", ghp_prev / version_sub_path, os.listdir(str(ghp_prev / version_sub_path)))
                    v_path = ghp_up / "rdf.yaml"
            else:
                v_path = gh_pages / version_sub_path / "rdf.yaml"

            if not v_path.exists():
                print(f"ignoring missing resource version {v_path}")
                continue

            this_version = yaml.load(v_path)
            if not isinstance(this_version, dict):
                print(f"ignoring non-dict resource version {v_path}")
                continue

            # add validation summaries
            val_summaries = {}
            for val_path in v_path.parent.glob("validation_summary_*.yaml"):
                name = val_path.stem.replace("validation_summary_", "")
                val_sum = yaml.load(val_path)
                if not isinstance(val_sum, dict):
                    val_sum = {"output": val_sum}

                val_summaries[name] = {k: v for k, v in val_sum.items() if k != "source_name"}

            this_version["validation_summaries"] = val_summaries

            if latest_version is None:
                latest_version = this_version
                latest_version["resource_id"] = r["resource_id"]
                latest_version["previous_versions"] = []
            else:
                latest_version["previous_versions"].append(this_version)

        if latest_version is None:
            print(f"Ignoring resource at {r_path} without any accepted versions")
        else:
            type_ = latest_version.get("type", "unknown")
            attachments[type_] = attachments.get(type_)
            type_list = attachments[type_]
            if isinstance(type_list, list):
                type_list.append(latest_version)
                n_accepted[type_] = n_accepted.get(type_, 0) + 1
                n_accepted_versions[type_] = (
                    n_accepted_versions.get(type_, 0) + 1 + len(latest_version["previous_versions"])
                )
            else:
                print(f"ignoring resource {r_path} with type '{type_}'")

    print(f"new collection rdf contains {sum(n_accepted.values())} accepted of {len(known_resources)} known resources.")
    print("accepted resources per type:")
    pprint(n_accepted)
    print("accepted resource versions per type:")
    pprint(n_accepted_versions)

    rdf["config"]["n_resources"] = n_accepted
    rdf["config"]["n_resource_versions"] = n_accepted_versions
    rdf_path = Path("dist/gh-pages-update/rdf.yaml")
    rdf_path.parent.mkdir(exist_ok=True)
    yaml.dump(rdf, rdf_path)

    def convert_for_json(p, k, v):
        """convert anything not json compatible"""
        # replace nans
        number_strings = ["-inf", "inf", "nan"]
        for n in number_strings:
            if v == float(n):
                return k, n

        if isinstance(v, datetime):
            return k, v.isoformat()

        return True

    rdf = remap(rdf, convert_for_json)
    with open(rdf_path.with_suffix(".json"), "w") as f:
        json.dump(rdf, f, allow_nan=False)

    set_gh_actions_output("processed_gh_pages_previews", json.dumps({"preview-branch": processed_gh_pages_previews}))
    set_gh_actions_output("processed_any_gh_pages_previews", "yes" if processed_gh_pages_previews else "no")
    return 0


if __name__ == "__main__":
    typer.run(main)
