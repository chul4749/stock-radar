"""site/data 의 기준일 파일 목록으로 index.json 을 다시 쓴다. 실행: python -m collector.reindex"""
import json
import os

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site", "data")


def write_index():
    dates = sorted(n[:8] for n in os.listdir(DATA) if n[:8].isdigit() and n.endswith(".json"))
    with open(os.path.join(DATA, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"dates": dates[::-1], "latest": dates[-1]}, f, ensure_ascii=False)
    return dates


if __name__ == "__main__":
    write_index()
