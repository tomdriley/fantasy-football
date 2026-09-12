import json
from pathlib import Path
import shutil
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
APP = "fantasy-probe"
SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
PACKAGE = "ghcr.io/tomdriley/fantasy-football-hosting"
IMAGE = f"{PACKAGE}@{DIGEST}"
CONTAINER_ID = "d" * 64
HOST = "fantasy-stage-unique.eastus-01.azurewebsites.net"
SLOT = (
    "/subscriptions/b9ee5d35-c096-4772-8a56-0529054b4dcf"
    f"/resourceGroups/WebResourceGroup2/providers/Microsoft.Web/sites/{APP}/slots/stage"
)
API = "?api-version=2024-11-01"

# Exported functions reach the child script, including command substitutions and
# cleanup traps. An empty PATH prevents falling through to real cloud/HTTP tools.
MOCK_COMMANDS = r"""
trace() {
  printf 'CALL' >&2
  printf '\t%s' "$@" >&2
  printf '\n' >&2
}
az() {
  trace az "$@"
  if [[ -n "$MOCK_AZ_FAILURE" && "$*" == *"--method $MOCK_AZ_FAILURE "* ]]; then
    return 83
  elif [[ "$*" == *'--query properties.defaultHostName'* ]]; then
    printf '%s\n' "$MOCK_HOST"
  elif [[ "$*" == *'--query properties.linuxFxVersion'* ]]; then
    printf '%s\n' "$MOCK_IMAGE"
  elif [[ "$*" == *'--method patch'* || "$*" == *'--method post'* ]]; then
    return 0
  else
    return 97
  fi
}
docker() {
  trace docker "$@"
  if [[ "$*" == *'.Config.User'* ]]; then
    printf '%s\n' "$MOCK_USER"
  elif [[ "$*" == *'.Architecture'* ]]; then
    printf '%s\n' "$MOCK_ARCH"
  elif [[ "$*" == *'org.opencontainers.image.revision'* ]]; then
    printf '%s\n' "$MOCK_RELEASE"
  elif [[ "$1" == run ]]; then
    if [[ "$MOCK_RUN_STATUS" != 0 ]]; then
      printf 'Container name already belongs to another container.\n' >&2
      return "$MOCK_RUN_STATUS"
    fi
    printf '%s\n' "$MOCK_CONTAINER_ID"
  elif [[ "$1" == port ]]; then
    printf '%s\n' "$MOCK_PORT"
  elif [[ "$1" == rm || "$1" == logs ]]; then
    return 0
  else
    return 98
  fi
}
python3() {
  trace python3 "$@"
  return "$MOCK_SMOKE_STATUS"
}
timeout() {
  trace timeout "$@"
  shift
  "$@"
}
sleep() { return 0; }
export -f trace az docker python3 timeout sleep
"$BASH" "$@"
"""


@unittest.skipUnless(BASH, "Deployment scripts require Bash")
class TestHostingDeployment(unittest.TestCase):
    def run_script(self, name, args, **overrides):
        environment = {
            "PATH": "",
            "LC_ALL": "C",
            "GITHUB_RUN_ID": "offline",
            "MOCK_AZ_FAILURE": "",
            "MOCK_HOST": HOST,
            "MOCK_IMAGE": f"DOCKER|{IMAGE}",
            "MOCK_RELEASE": SHA,
            "MOCK_USER": "10001:10001",
            "MOCK_ARCH": "amd64",
            "MOCK_PORT": "127.0.0.1:49153",
            "MOCK_CONTAINER_ID": CONTAINER_ID,
            "MOCK_RUN_STATUS": "0",
            "MOCK_SMOKE_STATUS": "0",
        }
        environment.update(overrides)
        return subprocess.run(
            [BASH, "-c", MOCK_COMMANDS, "mock", str(ROOT / "infra/azure" / name), *args],
            cwd=ROOT, env=environment, capture_output=True, text=True, timeout=10,
        )

    @staticmethod
    def calls(result, command):
        prefix = f"CALL\t{command}\t"
        return [
            line.split("\t")[2:]
            for line in result.stderr.splitlines()
            if line.startswith(prefix)
        ]

    @staticmethod
    def workflow(filename):
        path = ROOT / ".github/workflows" / filename
        return yaml.load(path.read_text(), Loader=yaml.BaseLoader)

    def test_stage_success_uses_only_expected_slot_rest_calls(self):
        result = self.run_script("deploy-stage.sh", [APP, SHA, DIGEST])
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls(result, "az")
        self.assertEqual(
            [(call[call.index("--method") + 1], call[call.index("--url") + 1])
             for call in calls],
            [
                ("get", SLOT + API),
                ("get", SLOT + "/config/web" + API),
                ("patch", SLOT + "/config/web" + API),
                ("post", SLOT + "/restart" + API),
                ("get", SLOT + "/config/web" + API),
                ("get", SLOT + "/config/web" + API),
            ],
        )
        patch = calls[2]
        self.assertEqual(json.loads(patch[patch.index("--body") + 1]), {
            "properties": {"linuxFxVersion": f"DOCKER|{IMAGE}"},
        })
        self.assertEqual(self.calls(result, "python3"), [[
            "scripts/check_hosted_app.py", f"https://{HOST}",
            "--expected-environment", "stage", "--expected-release", SHA,
        ]])
        self.assertEqual(self.calls(result, "timeout")[0][:2], ["20s", "python3"])
        self.assertIn(f"Verified stage release {SHA}", result.stdout)

    def test_bad_deployment_inputs_never_reach_azure(self):
        cases = [
            ["blog/slots/production", SHA, DIGEST],
            ["fantasy-probe;echo unexpected", SHA, DIGEST],
            [APP, SHA.upper(), DIGEST],
            [APP, "development", DIGEST],
            [APP, SHA, "latest"],
            [APP, SHA, "sha256:" + "g" * 64],
            [APP, SHA, "sha256:" + "b" * 63],
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self.run_script("deploy-stage.sh", args)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.calls(result, "az"), result.stderr)

    def test_bad_host_aborts_before_image_update(self):
        for host in ("", "evil.example", "stage.azurewebsites.net/path",
                     "stage.azurewebsites.net;echo unexpected"):
            with self.subTest(host=host):
                result = self.run_script("deploy-stage.sh", [APP, SHA, DIGEST], MOCK_HOST=host)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(self.calls(result, "az")), 1, result.stderr)
                self.assertFalse(self.calls(result, "python3"))

    def test_wrong_configured_digest_fails_before_smoke(self):
        result = self.run_script(
            "deploy-stage.sh", [APP, SHA, DIGEST], MOCK_IMAGE=f"DOCKER|{PACKAGE}@sha256:" + "c" * 64,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls(result, "python3"))
        self.assertIn("did not match the requested digest", result.stderr)

    def test_stage_smoke_errors_exhaust_bounded_retries_without_rollback(self):
        for status in ("1", "124"):
            with self.subTest(status=status):
                result = self.run_script(
                    "deploy-stage.sh", [APP, SHA, DIGEST], MOCK_SMOKE_STATUS=status,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(self.calls(result, "python3")), 24)
                calls = self.calls(result, "az")
                self.assertEqual(sum("patch" in call for call in calls), 1)
                self.assertEqual(sum("post" in call for call in calls), 1)
                self.assertFalse(any("swap" in argument for call in calls for argument in call))
                self.assertNotIn("Verified stage release", result.stdout)

    def test_azure_command_failure_stops_deployment(self):
        for method in ("get", "patch", "post"):
            with self.subTest(method=method):
                result = self.run_script(
                    "deploy-stage.sh", [APP, SHA, DIGEST], MOCK_AZ_FAILURE=method,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.calls(result, "python3"))
                calls = self.calls(result, "az")
                self.assertEqual(calls[-1][calls[-1].index("--method") + 1], method)

    def test_a_b_a_uses_original_digest_and_release_without_building(self):
        for letter in ("a", "b", "a"):
            release = letter * 40
            digest = "sha256:" + letter * 64
            image = f"{PACKAGE}@{digest}"
            with self.subTest(release=release):
                result = self.run_script(
                    "deploy-stage.sh", [APP, release, digest], MOCK_IMAGE=f"DOCKER|{image}",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                patch = next(call for call in self.calls(result, "az") if "patch" in call)
                self.assertEqual(json.loads(patch[patch.index("--body") + 1]), {
                    "properties": {"linuxFxVersion": f"DOCKER|{image}"},
                })
                self.assertEqual(self.calls(result, "python3")[0][-1], release)
                self.assertFalse(self.calls(result, "docker"))

    def test_container_smoke_checks_hardened_runtime_and_cleans_up_its_id(self):
        result = self.run_script("check-image.sh", [IMAGE, SHA])
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls(result, "docker")
        run = next(call for call in calls if call[0] == "run")
        for flag in ("--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges"):
            self.assertIn(flag, run)
        self.assertEqual(run[run.index("--publish") + 1], "127.0.0.1::8080")
        self.assertEqual(run[run.index("--env") + 1], "FFOPT_HOSTING_ENVIRONMENT=local")
        self.assertEqual([call for call in calls if call[0] == "port"], [
            ["port", CONTAINER_ID, "8080/tcp"],
        ])
        self.assertEqual([call for call in calls if call[0] == "rm"], [
            ["rm", "--force", CONTAINER_ID],
        ])
        self.assertEqual(self.calls(result, "python3"), [[
            "scripts/check_hosted_app.py", "http://127.0.0.1:49153", "--allow-http",
            "--expected-environment", "local", "--expected-release", SHA,
        ]])
        self.assertEqual(self.calls(result, "timeout")[0][:2], ["20s", "python3"])

    def test_failed_docker_run_never_removes_preexisting_named_container(self):
        result = self.run_script("check-image.sh", [IMAGE, SHA], MOCK_RUN_STATUS="125")
        self.assertNotEqual(result.returncode, 0)
        calls = self.calls(result, "docker")
        self.assertEqual(sum(call[0] == "run" for call in calls), 1)
        self.assertFalse(any(call[0] in ("rm", "port", "logs") for call in calls), result.stderr)
        self.assertFalse(self.calls(result, "python3"))

    def test_unacceptable_container_is_rejected(self):
        cases = [
            {"MOCK_USER": "root"},
            {"MOCK_ARCH": "arm64"},
            {"MOCK_RELEASE": "c" * 40},
            {"MOCK_PORT": "0.0.0.0:49153"},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                result = self.run_script("check-image.sh", [IMAGE, SHA], **overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.calls(result, "python3"))
                removals = [call for call in self.calls(result, "docker") if call[0] == "rm"]
                self.assertEqual(
                    removals, [["rm", "--force", CONTAINER_ID]] if "MOCK_PORT" in overrides else [],
                )
        result = self.run_script("check-image.sh", [IMAGE, "development"])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls(result, "docker"))

    def test_container_smoke_errors_exhaust_retries_and_remove_only_its_id(self):
        for status in ("1", "124"):
            with self.subTest(status=status):
                result = self.run_script("check-image.sh", [IMAGE, SHA], MOCK_SMOKE_STATUS=status)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(self.calls(result, "python3")), 30)
                calls = self.calls(result, "docker")
                self.assertIn(["logs", "--tail", "40", CONTAINER_ID], calls)
                self.assertEqual([call for call in calls if call[0] == "rm"], [
                    ["rm", "--force", CONTAINER_ID],
                ])

    def test_workflow_shell_pins_permissions_and_manual_dispatch_guards(self):
        for name in ("hosting-ci.yml", "hosting-stage.yml"):
            workflow = self.workflow(name)
            for job in workflow["jobs"].values():
                for step in job["steps"]:
                    if "uses" in step:
                        self.assertRegex(step["uses"], r"^[^@]+@[0-9a-f]{40}$")
                        if step["uses"].startswith("actions/checkout@"):
                            self.assertEqual(step["with"]["persist-credentials"], "false")
                    if "run" in step:
                        result = subprocess.run(
                            [BASH, "-n"], input=step["run"], text=True, capture_output=True,
                            env={"PATH": "", "LC_ALL": "C"}, timeout=10,
                        )
                        self.assertEqual(result.returncode, 0, (name, result.stderr))
        ci = self.workflow("hosting-ci.yml")
        self.assertEqual(set(ci["on"]), {"pull_request", "workflow_dispatch"})
        self.assertEqual(ci["permissions"], {"contents": "read"})
        stage = self.workflow("hosting-stage.yml")
        self.assertEqual(set(stage["on"]), {"workflow_dispatch"})
        self.assertEqual(stage["permissions"], {})
        self.assertEqual(stage["concurrency"]["cancel-in-progress"], "false")
        for name, job in stage["jobs"].items():
            self.assertIn("github.repository == 'tomdriley/fantasy-football'", job["if"])
            self.assertIn("github.ref == 'refs/heads/main'", job["if"])
            self.assertIn("github.actor == 'tomdriley'", job["if"])
            self.assertIn("github.triggering_actor == 'tomdriley'", job["if"])
            self.assertEqual(job["permissions"], (
                {"contents": "read", "packages": "write"} if name == "build-publish"
                else {"contents": "read", "id-token": "write"}
            ))

    def test_deployment_preflight_rejects_wrong_target_or_artifact(self):
        workflow = self.workflow("hosting-stage.yml")
        code = workflow["jobs"]["digest-deploy"]["steps"][0]["run"]
        environment = {
            "PATH": "", "LC_ALL": "C", "EXPECTED_RELEASE": SHA, "IMAGE_DIGEST": DIGEST,
            "TARGET_APP": APP, "APPROVED_APP": APP,
        }
        cases = [
            ({}, True),
            ({"APPROVED_APP": ""}, False),
            ({"TARGET_APP": "existing-blog"}, False),
            ({"TARGET_APP": "fantasy-probe;echo unexpected"}, False),
            ({"IMAGE_DIGEST": "latest"}, False),
            ({"EXPECTED_RELEASE": "development"}, False),
        ]
        for overrides, succeeds in cases:
            with self.subTest(overrides=overrides):
                result = subprocess.run(
                    [BASH, "-e", "-c", code], env={**environment, **overrides},
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)

    def test_publication_retains_only_the_image_review_artifact(self):
        steps = self.workflow("hosting-stage.yml")["jobs"]["build-publish"]["steps"]
        upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertEqual(upload["with"]["path"], "${{ runner.temp }}/hosting-image-review/")
        self.assertEqual(upload["with"]["retention-days"], "3")
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        self.assertEqual(upload["with"]["name"], "hosting-image-review-${{ github.sha }}")
        publish = next(step["run"] for step in steps if "docker push" in step.get("run", ""))
        self.assertIn('docker image save "$tag" --output "$review_dir/image.tar"', publish)
        self.assertIn('review_dir="$RUNNER_TEMP/hosting-image-review"', publish)
        self.assertIn('"$review_dir/reference.txt"', publish)
        self.assertIn('"$review_dir/release.txt"', publish)


if __name__ == "__main__":
    unittest.main()
