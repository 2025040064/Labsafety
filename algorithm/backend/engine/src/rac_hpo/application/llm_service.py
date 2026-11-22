"""PyTRIO-backed LLM post-training service.

This module is deliberately isolated from the tabular/industrial pipelines.
It drives the PyTRIO Training API (LoRA SFT + sampling) through a locally
written training loop: the mini-program owns the data and the algorithm, while
PyTRIO supplies the remote GPU compute.

No API key is ever written to the workspace or to job logs. A key is taken
from the request at call time, or from the PYTRIO_API_KEY environment variable.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BASE_MODEL = "Qwen/Qwen3.5-4B"
DEFAULT_EXAMPLES = [
    {"input": "what is trio", "output": "trio is emotionmachine's AI Infra products."},
    {"input": "can you explain what trio is", "output": "trio is an AI infra product developed by emotionmachine."},
    {"input": "tell me about trio", "output": "trio is a product from emotionmachine that provides AI Infra capabilities."},
]
MODEL_RE = re.compile(r"^[A-Za-z0-9._/-]{1,80}$")
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _key(api_key=None):
    key = api_key or os.environ.get("PYTRIO_API_KEY") or ""
    key = key.strip()
    if not key:
        raise ValueError("未配置 PyTRIO API key：请在表单中填写，或设置环境变量 PYTRIO_API_KEY")
    return key


def list_runs(root):
    """Expose persisted LLM runs through the same run list as tabular jobs."""
    directory = Path(root) / "workspace" / "llm_runs"
    rows = []
    for path in sorted(directory.glob("LLM-*/result.json"), reverse=True):
        try:
            detail = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows.append({
            "id": detail.get("id") or path.parent.name,
            "dataset_key": "sft_examples",
            "algorithm": "lora_sft",
            "model_name": detail.get("base_model"),
            "status": detail.get("status"),
            "best_value": detail.get("final_loss"),
            "target_unit": "loss",
            "created_at": detail.get("created_at"),
        })
    return rows


def check_availability(api_key=None):
    """Verify SDK + key without launching any paid compute."""
    try:
        import pytrio as trio  # noqa: F401
    except ImportError:
        return {"sdk": False, "key": bool(api_key or os.environ.get("PYTRIO_API_KEY")),
                "message": "pytrio SDK 未安装"}
    return {"sdk": True, "key": bool(api_key or os.environ.get("PYTRIO_API_KEY")),
            "message": "SDK 就绪" if (api_key or os.environ.get("PYTRIO_API_KEY"))
                       else "SDK 就绪但未配置 API key"}


def _prompt(example, template):
    return template.replace("{input}", example["input"])


def run_sft(root, *, api_key=None, base_model=DEFAULT_BASE_MODEL, examples=None,
            iterations=5, learning_rate=1e-4, rank=8, name="lab-sft",
            template="Question: {input}\nAnswer:", evaluate=True,
            max_tokens=40, progress=None, cancel_check=None):
    """Run a minimal LoRA SFT job and return a persisted result summary."""
    key = _key(api_key)
    # The SDK captures its credential at import time, so set the worker-local
    # environment variable before importing it. The explicit api_key argument
    # below is the authoritative credential for this client.
    # The worker is a separate process, so this never leaks to the server.
    os.environ["PYTRIO_API_KEY"] = key
    import pytrio as trio

    if not isinstance(base_model, str) or not MODEL_RE.fullmatch(base_model):
        raise ValueError("Invalid base model")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError("Invalid run name")
    if type(iterations) is not int or not 1 <= iterations <= 100:
        raise ValueError("Invalid iterations: expected 1–100")
    if not isinstance(learning_rate, (int, float)) or not 1e-6 <= learning_rate <= 1e-1:
        raise ValueError("Invalid learning rate")
    if type(rank) is not int or not 4 <= rank <= 64:
        raise ValueError("Invalid LoRA rank: expected 4–64")

    rows = examples or DEFAULT_EXAMPLES
    if not isinstance(rows, list) or not rows:
        raise ValueError("Examples must be a non-empty list")
    cleaned = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each example must be an object")
        q, a = row.get("input"), row.get("output")
        if not isinstance(q, str) or not isinstance(a, str) or not q.strip() or not a.strip():
            raise ValueError("Each example needs non-empty input and output")
        if len(q) > 2000 or len(a) > 2000:
            raise ValueError("Example input/output too long (max 2000 chars)")
        cleaned.append({"input": q.strip(), "output": a.strip()})

    report = lambda payload: progress(payload) if progress else None
    halt = lambda: bool(cancel_check and cancel_check())
    run_id = "LLM-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = Path(root) / "workspace" / "llm_runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    report({"type": "llm_status", "phase": "connect", "message": "连接 PyTRIO 计算引擎"})
    service = trio.ServiceClient(api_key=key)
    supported = list(service.get_supported_models() or [])
    if base_model not in supported:
        raise ValueError("模型 %s 不在 PyTRIO 支持列表；可用：%s" %
                         (base_model, ", ".join(supported[:8]) or "（无）"))

    report({"type": "llm_status", "phase": "client", "message": "创建 LoRA 训练客户端"})
    training = service.create_lora_training_client(base_model=base_model, rank=rank)
    tokenizer = training.get_tokenizer()

    def to_datum(example):
        prompt = _prompt(example, template)
        p_tokens = tokenizer.encode(prompt, add_special_tokens=True)
        c_tokens = tokenizer.encode(" " + example["output"] + "\n\n", add_special_tokens=False)
        tokens = p_tokens + c_tokens
        weights = [0] * len(p_tokens) + [1] * len(c_tokens)
        return trio.Datum(
            model_input=trio.ModelInput.from_ints(tokens=tokens[:-1]),
            loss_fn_inputs={"weights": weights[1:], "target_tokens": tokens[1:]},
        )

    data = [to_datum(example) for example in cleaned]
    losses = []
    for step in range(iterations):
        if halt():
            raise RuntimeError("Training cancelled")
        fwd = training.forward_backward(data, "cross_entropy")
        opt = training.optim_step(trio.AdamParams(learning_rate=float(learning_rate)))
        fwd_result = fwd.result()
        opt.result()
        # TensorData is a pydantic model: the numeric vector lives in `.data`.
        total_w = sum(float(sum(example.loss_fn_inputs["weights"].data)) for example in data)
        # logprobs are negative; loss = -sum(logprob * weight) / sum(weight)
        loss = 0.0
        for example, output in zip(data, fwd_result.loss_fn_outputs):
            logprobs = output["logprobs"].data
            weights = example.loss_fn_inputs["weights"].data
            loss += -sum(float(lp) * float(w) for lp, w in zip(logprobs, weights))
        loss = loss / total_w if total_w else 0.0
        losses.append(round(loss, 6))
        report({"type": "llm_progress", "iteration": step + 1, "total": iterations,
                "loss": round(loss, 6)})

    report({"type": "llm_status", "phase": "save", "message": "保存推理权重"})
    weights = training.save_weights_for_sampler(name=name).result()

    generated = {"base": None, "sft": None}
    if evaluate:
        report({"type": "llm_status", "phase": "sample", "message": "训练前后采样对比"})
        prompt_text = _prompt(cleaned[0], template)
        prompt = trio.ModelInput.from_ints(tokenizer.encode(prompt_text))
        params = trio.SamplingParams(max_tokens=max_tokens, temperature=0.0, seed=42)
        base_client = service.create_sampling_client(base_model=base_model)
        sft_client = service.create_sampling_client(base_model=base_model, model_path=weights.path)
        generated["base"] = base_client.sample(prompt=prompt, sampling_params=params,
                                                num_samples=1).result().sequences[0].text
        generated["sft"] = sft_client.sample(prompt=prompt, sampling_params=params,
                                              num_samples=1).result().sequences[0].text

    result = {
        "id": run_id,
        "status": "completed",
        "provider": "pytrio",
        "base_model": base_model,
        "lora_rank": rank,
        "iterations": iterations,
        "learning_rate": learning_rate,
        "examples": cleaned,
        "losses": losses,
        "final_loss": losses[-1] if losses else None,
        "sample_prompt": _prompt(cleaned[0], template),
        "generated": generated,
        "weights_path": weights.path,
        "directory": str(out_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report({"type": "llm_status", "phase": "done", "message": "训练完成"})
    return result
