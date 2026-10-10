"""Aggregate the live CPU/RAM metrics reported by connected agents.

Agents push a `metrics` message every few seconds over the WebSocket; the hub
keeps the latest per MAC. This service turns that snapshot into fleet totals and
a per-node list for the dashboard.
"""

from __future__ import annotations


class FleetMetrics:
    @staticmethod
    def snapshot() -> dict:
        from core.websocket import get_hub
        from resources.app.models.node import Node

        metrics = get_hub().metrics()

        rows: list[dict] = []
        for mac, data in metrics.items():
            node = Node.find_by_mac(mac)
            rows.append(
                {
                    "mac": mac,
                    "name": node.name if node is not None else mac,
                    "cpu_percent": float(data.get("cpu_percent") or 0.0),
                    "ram_percent": float(data.get("ram_percent") or 0.0),
                    "ram_used_gb": float(data.get("ram_used_gb") or 0.0),
                    "ram_total_gb": float(data.get("ram_total_gb") or 0.0),
                }
            )
        rows.sort(key=lambda row: row["name"].lower())

        count = len(rows)
        cpu_sum = round(sum(row["cpu_percent"] for row in rows), 1)
        ram_used = round(sum(row["ram_used_gb"] for row in rows), 1)
        ram_total = round(sum(row["ram_total_gb"] for row in rows), 1)
        return {
            "count": count,
            "cpu_sum": cpu_sum,
            "cpu_avg": round(cpu_sum / count, 1) if count else 0.0,
            "ram_used_gb": ram_used,
            "ram_total_gb": ram_total,
            "ram_percent": round(ram_used / ram_total * 100, 1) if ram_total else 0.0,
            "rows": rows,
        }
