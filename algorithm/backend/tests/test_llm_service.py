"""Offline tests for the PyTRIO SFT bridge (no network or paid compute)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine/src"))

from rac_hpo.application import llm_service


class TensorData:
    def __init__(self, data, dtype="float32"):
        self.data = list(data)
        self.dtype = dtype


class ModelInput:
    def __init__(self, tokens):
        self.tokens = list(tokens)

    @classmethod
    def from_ints(cls, tokens):
        return cls(tokens)


class Datum:
    def __init__(self, model_input, loss_fn_inputs):
        self.model_input = model_input
        self.loss_fn_inputs = {
            key: value if isinstance(value, TensorData) else TensorData(value)
            for key, value in loss_fn_inputs.items()
        }


class _Result:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value() if callable(self._value) else self._value


class Tokenizer:
    def encode(self, text, add_special_tokens=True):
        return [1, 2] if add_special_tokens else [3, 4]


class Training:
    def get_tokenizer(self):
        return Tokenizer()

    def forward_backward(self, data, loss_fn):
        return _Result(lambda: types.SimpleNamespace(
            loss_fn_outputs=[{"logprobs": TensorData([-1.0, -2.0, -3.0])} for _ in data]))

    def optim_step(self, params):
        return _Result(None)

    def save_weights_for_sampler(self, name):
        return _Result(types.SimpleNamespace(path="fake://weights"))


class Sampling:
    def sample(self, **kwargs):
        return _Result(types.SimpleNamespace(
            sequences=[types.SimpleNamespace(text="generated text")]))


class ServiceClient:
    instances = []

    def __init__(self, api_key=None):
        self.api_key = api_key
        ServiceClient.instances.append(self)

    def get_supported_models(self):
        return ["Qwen/Qwen3.5-4B"]

    def create_lora_training_client(self, base_model, rank):
        return Training()

    def create_sampling_client(self, base_model, model_path=None):
        return Sampling()


def fake_pytrio():
    module = types.ModuleType("pytrio")
    module.TensorData = TensorData
    module.ModelInput = ModelInput
    module.Datum = Datum
    module.AdamParams = lambda learning_rate: {"lr": learning_rate}
    module.SamplingParams = lambda **kwargs: kwargs
    module.ServiceClient = ServiceClient
    return module


class LlmServiceTest(unittest.TestCase):
    def setUp(self):
        ServiceClient.instances.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.modules = patch.dict(sys.modules, {"pytrio": fake_pytrio()})
        self.modules.start()
        self.env = patch.dict(os.environ, {"PYTRIO_API_KEY": "test-key"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.modules.stop()
        self.temp.cleanup()

    def test_availability_reports_key_from_request_or_environment(self):
        with patch.dict(os.environ, {"PYTRIO_API_KEY": ""}):
            self.assertEqual(llm_service.check_availability("k"),
                             {"sdk": True, "key": True, "message": "SDK 就绪"})
            self.assertEqual(llm_service.check_availability(),
                             {"sdk": True, "key": False,
                              "message": "SDK 就绪但未配置 API key"})

    def test_empty_key_is_rejected_before_any_sdk_call(self):
        with patch.dict(os.environ, {"PYTRIO_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "未配置 PyTRIO API key"):
                llm_service.run_sft(self.root, api_key="")

    def test_invalid_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            llm_service.run_sft(self.root, iterations=0)
        with self.assertRaises(ValueError):
            llm_service.run_sft(self.root, rank=2)
        with self.assertRaises(ValueError):
            llm_service.run_sft(self.root, rank=65)

    def test_sft_uses_credential_and_computes_weighted_loss(self):
        result = llm_service.run_sft(self.root, api_key="explicit-key",
                                     iterations=1, evaluate=True)
        self.assertEqual(ServiceClient.instances[0].api_key, "explicit-key")
        # weights [0, 1, 1], logprobs [-1, -2, -3] => (2 + 3) / 2 = 2.5
        self.assertEqual(result["final_loss"], 2.5)
        self.assertEqual(result["losses"], [2.5])
        self.assertEqual(result["generated"]["base"], "generated text")
        self.assertTrue((Path(result["directory"]) / "result.json").is_file())
        saved = json.loads((Path(result["directory"]) / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["id"], result["id"])
        self.assertNotIn("api_key", saved)

    def test_list_runs_exposes_llm_results(self):
        directory = self.root / "workspace" / "llm_runs" / "LLM-test"
        directory.mkdir(parents=True)
        (directory / "result.json").write_text(json.dumps({
            "id": "LLM-test", "status": "completed", "base_model": "Qwen/Qwen3.5-4B",
            "final_loss": 1.5, "created_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
        rows = llm_service.list_runs(self.root)
        self.assertEqual(rows[0]["id"], "LLM-test")
        self.assertEqual(rows[0]["algorithm"], "lora_sft")
        self.assertEqual(rows[0]["best_value"], 1.5)
        self.assertEqual(rows[0]["target_unit"], "loss")


if __name__ == "__main__":
    unittest.main()
