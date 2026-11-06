"""Default model settings from .env, per-agent overrides, and keeping keys out of the logs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from try_professional.adapters.api_runner.clients import ChatRequest, ClientError, OpenAICompatibleClient, redact
from try_professional.adapters.api_runner.runner import ApiRunner
from try_professional.adapters.api_runner.scripted import ScriptedClient
from try_professional.config import (
    CONFIG_TEMPLATE,
    ClientRouter,
    ModelConfig,
    load_config,
    load_env_file,
    parse_env_file,
)
from try_professional.core.orchestrator import Orchestrator
from try_professional.org import Org, Role
from try_professional.org.loader import read_documents, validate_bundle

DEFAULT = ModelConfig(
    provider="openai_compatible",
    tiers={},
    api_key_env="TRYPRO_LLM_API_KEY",
    base_url="https://default.example.com/v1",
    default_model="cheap-model",
)


@pytest.fixture(autouse=True)
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRYPRO_LLM_API_KEY", "default-key-value-123456")
    monkeypatch.setenv("QA_LLM_KEY", "qa-key-value-123456")
    monkeypatch.setenv("LEGAL_LLM_KEY", "legal-key-value-123456")


def _with_overrides(overrides: dict[str, Any]) -> Callable[[Path], None]:
    def mutate(org_dir: Path) -> None:
        path = org_dir / "org.yaml"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        doc["llm_overrides"] = overrides
        path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    return mutate


class TestEnvFile:
    def test_parses_common_forms(self) -> None:
        parsed = parse_env_file("# note\nA=1\nexport B=two\nC='three'\nD=\"four\"\n\nnot a line\nE=a=b\n")
        assert parsed == {"A": "1", "B": "two", "C": "three", "D": "four", "E": "a=b"}

    def test_real_environment_wins_over_the_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".env").write_text("TRY_X=from-file\nTRY_Y=from-file\n", encoding="utf-8")
        monkeypatch.setenv("TRY_X", "from-env")
        monkeypatch.delenv("TRY_Y", raising=False)
        load_env_file(tmp_path)
        assert __import__("os").environ["TRY_X"] == "from-env"
        assert __import__("os").environ["TRY_Y"] == "from-file"
        monkeypatch.delenv("TRY_Y")

    def test_finds_the_nearest_file_above(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".env").write_text("TRY_Z=up\n", encoding="utf-8")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        monkeypatch.delenv("TRY_Z", raising=False)
        assert load_env_file(nested) == tmp_path / ".env"
        monkeypatch.delenv("TRY_Z")

    def test_project_defaults_come_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "trypro.yaml").write_text(CONFIG_TEMPLATE, encoding="utf-8")
        monkeypatch.setenv("TRYPRO_LLM_BASE_URL", "https://llm.example.com/v1")
        monkeypatch.setenv("TRYPRO_LLM_MODEL", "cheap-model")
        config = load_config(tmp_path).model
        assert config.provider == "openai_compatible"
        assert config.base_url == "https://llm.example.com/v1"
        assert config.api_key_env == "TRYPRO_LLM_API_KEY"
        assert config.model_for_tier("executive") == "cheap-model"

    def test_openai_api_key_is_never_used(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "should-never-be-used-123456")
        monkeypatch.delenv("TRYPRO_LLM_API_KEY")
        (tmp_path / "trypro.yaml").write_text(CONFIG_TEMPLATE, encoding="utf-8")
        monkeypatch.setenv("TRYPRO_LLM_BASE_URL", "https://llm.example.com/v1")
        config = load_config(tmp_path).model
        assert "OPENAI_API_KEY" not in CONFIG_TEMPLATE
        with pytest.raises(ClientError, match="TRYPRO_LLM_API_KEY"):
            OpenAICompatibleClient(base_url=config.base_url or "", api_key_env=config.api_key_env)

    def test_missing_model_says_what_to_set(self) -> None:
        config = ModelConfig(provider="openai_compatible", tiers={})
        with pytest.raises(ClientError, match="TRYPRO_LLM_MODEL"):
            config.model_for_tier("standard")


class TestOverrides:
    def test_default_is_the_same_for_everyone(self, org: Org) -> None:
        router = ClientRouter(DEFAULT, org)
        routes = {router.resolve(role) for role in org.roles.values()}
        assert len(routes) == 1
        only = routes.pop()
        assert (only.model, only.api_key_env, only.overridden) == ("cheap-model", "TRYPRO_LLM_API_KEY", False)

    def test_role_wins_over_department_field_by_field(self, make_org: Callable[..., Org]) -> None:
        org = make_org(
            mutate=_with_overrides(
                {
                    "engineering": {"model": "dept-model"},
                    "engineering/qa": {"api_key_env": "QA_LLM_KEY"},
                    "engineering/devops": {"model": "devops-model"},
                }
            )
        )
        router = ClientRouter(DEFAULT, org)
        backend = router.resolve(org.role("engineering/backend"))
        qa = router.resolve(org.role("engineering/qa"))
        devops = router.resolve(org.role("engineering/devops"))
        sales = router.resolve(org.role("sales/sales-lead"))
        assert (backend.model, backend.api_key_env) == ("dept-model", "TRYPRO_LLM_API_KEY")
        assert (qa.model, qa.api_key_env) == ("dept-model", "QA_LLM_KEY")
        assert devops.model == "devops-model"
        assert (sales.model, sales.api_key_env, sales.overridden) == ("cheap-model", "TRYPRO_LLM_API_KEY", False)

    def test_each_key_goes_to_its_own_endpoint(self, make_org: Callable[..., Org]) -> None:
        org = make_org(
            mutate=_with_overrides(
                {"legal": {"base_url": "https://other.example.com/v1", "api_key_env": "LEGAL_LLM_KEY", "model": "m2"}}
            )
        )
        router = ClientRouter(DEFAULT, org)
        default_client, _ = router.route(org.role("engineering/backend"))
        legal_client, model = router.route(org.role("legal/counsel"))
        assert model == "m2"
        assert default_client is not legal_client
        assert isinstance(legal_client, OpenAICompatibleClient)
        assert legal_client._http.headers["authorization"] == "Bearer legal-key-value-123456"
        assert str(legal_client._http.base_url) == "https://other.example.com/v1/"

    def test_clients_are_shared_when_only_the_model_differs(self, make_org: Callable[..., Org]) -> None:
        org = make_org(mutate=_with_overrides({"sales": {"model": "m2"}}))
        router = ClientRouter(DEFAULT, org)
        assert router.route(org.role("sales/sales-lead"))[0] is router.route(org.role("product/ux-researcher"))[0]

    def test_a_missing_key_only_matters_if_that_role_runs(
        self, make_org: Callable[..., Org], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("QA_LLM_KEY")
        org = make_org(mutate=_with_overrides({"sales/sales-lead": {"api_key_env": "QA_LLM_KEY"}}))
        router = ClientRouter(DEFAULT, org)
        router.route(org.role("engineering/backend"))
        with pytest.raises(ClientError, match="QA_LLM_KEY"):
            router.route(org.role("sales/sales-lead"))

    def test_runner_uses_the_routed_model_for_each_role(self, org: Org, tmp_path: Path) -> None:
        models: dict[str, str] = {}
        scripted = ScriptedClient()

        class Recording:
            def route(self, role: Role) -> tuple[Any, str]:
                return scripted, f"model-for-{role.id}"

        original = scripted.complete

        def complete(request: ChatRequest) -> Any:
            models[request.metadata["invocation"].role] = request.model
            return original(request)

        scripted.complete = complete  # type: ignore[method-assign]
        result = ApiRunner(Orchestrator(org, tmp_path), router=Recording()).run("Goal")
        assert result.status == "done"
        assert models["executive/ceo"] == "model-for-ceo"
        assert models["engineering/backend"] == "model-for-backend"

    def test_runner_escalates_when_a_role_has_no_usable_key(
        self, make_org: Callable[..., Org], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TRYPRO_LLM_API_KEY")
        org = make_org()
        result = ApiRunner(Orchestrator(org, tmp_path), router=ClientRouter(DEFAULT, org)).run("Goal")
        assert result.status == "blocked"
        assert "TRYPRO_LLM_API_KEY" in result.questions[0].question


class TestOverrideSafety:
    """Org files get shared and forked, so they must not be a way to leak a key."""

    def _issues(self, make_org: Callable[..., Org], overrides: dict[str, Any], tmp_path: Path) -> set[str]:
        org = make_org()
        documents = read_documents(Path(next(iter(tmp_path.glob("org*")))))
        documents[0]["llm_overrides"] = overrides
        _ = org
        return {i.code for i in validate_bundle(*documents) if i.severity == "error"}

    def test_a_literal_key_field_is_rejected(self, make_org: Callable[..., Org], tmp_path: Path) -> None:
        assert self._issues(make_org, {"sales": {"api_key": "abc"}}, tmp_path) == {"SCHEMA"}

    def test_a_key_pasted_into_api_key_env_is_rejected(self, make_org: Callable[..., Org], tmp_path: Path) -> None:
        assert self._issues(make_org, {"sales": {"api_key_env": "sk-abcdef123456"}}, tmp_path) == {"SCHEMA"}

    def test_new_endpoint_cannot_inherit_the_default_key(self, make_org: Callable[..., Org], tmp_path: Path) -> None:
        overrides = {"sales": {"base_url": "https://attacker.example.com/v1"}}
        assert self._issues(make_org, overrides, tmp_path) == {"SCHEMA"}

    def test_plain_http_is_rejected_for_remote_endpoints(self, make_org: Callable[..., Org], tmp_path: Path) -> None:
        overrides = {"sales": {"base_url": "http://example.com/v1", "api_key_env": "QA_LLM_KEY"}}
        assert self._issues(make_org, overrides, tmp_path) == {"SCHEMA"}

    def test_a_local_server_may_use_http_and_no_key(self, make_org: Callable[..., Org], tmp_path: Path) -> None:
        overrides = {"sales": {"base_url": "http://localhost:8000/v1", "api_key_env": None, "model": "m"}}
        assert self._issues(make_org, overrides, tmp_path) == set()

    def test_override_for_an_absent_department_only_warns(self, make_org: Callable[..., Org], tmp_path: Path) -> None:
        org = make_org()
        documents = read_documents(Path(next(iter(tmp_path.glob("org*")))))
        documents[0]["llm_overrides"] = {"astrology": {"model": "m"}}
        _ = org
        issues = validate_bundle(*documents)
        assert [(i.code, i.severity) for i in issues] == [("ORG102", "warning")]


class TestKeysStayOutOfLogs:
    def test_redact(self) -> None:
        assert redact("bad key abc123 here", "abc123") == "bad key *** here"
        assert redact("nothing", None) == "nothing"

    def test_provider_echoing_the_key_is_redacted(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="Invalid key: default-key-value-123456")

        client = OpenAICompatibleClient(
            base_url="https://x.example.com/v1",
            api_key_env="TRYPRO_LLM_API_KEY",
            transport=httpx.MockTransport(handler),
        )
        request = ChatRequest(system="s", messages=[], tools=[], model="m")
        with pytest.raises(ClientError) as caught:
            client.complete(request)
        assert "default-key-value-123456" not in str(caught.value)
        assert "***" in str(caught.value)

    def test_a_failed_run_keeps_the_key_out_of_the_audit_log(self, org: Org, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="Invalid key: default-key-value-123456")

        client = OpenAICompatibleClient(
            base_url="https://x.example.com/v1",
            api_key_env="TRYPRO_LLM_API_KEY",
            transport=httpx.MockTransport(handler),
        )

        class Route:
            def route(self, role: Role) -> tuple[Any, str]:
                return client, "m"

        orchestrator = Orchestrator(org, tmp_path)
        result = ApiRunner(orchestrator, router=Route()).run("Goal")
        assert result.status == "blocked"
        for file in tmp_path.rglob("*"):
            if file.is_file():
                assert b"default-key-value-123456" not in file.read_bytes(), file.name
