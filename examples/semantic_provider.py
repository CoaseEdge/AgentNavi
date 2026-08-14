#!/usr/bin/env python3
"""AgentNavi 外部语义提供器最小示例。

这个示例不调用模型，只演示 stdin/stdout 协议。实际使用时可把规则替换为
本地模型或企业模型网关，并保持输出字段和证据约束。
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    payload = json.load(sys.stdin)
    physical_edges = payload.get("physical_edges", [])
    payment_files = sorted(
        {
            edge["source"]
            for edge in physical_edges
            if "payment" in edge.get("source", "").lower()
        }
        | {
            edge["target"]
            for edge in physical_edges
            if "payment" in edge.get("target", "").lower()
        }
    )
    result = {"concepts": [], "relations": []}
    if payment_files:
        result["concepts"].append(
            {
                "key": "payment",
                "label": "支付系统",
                "files": payment_files,
                "confidence": 0.78,
                "data": {"reason": "示例规则：路径包含 payment"},
            }
        )
    json.dump(result, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
