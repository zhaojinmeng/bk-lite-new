# -- coding: utf-8 --
"""IP 发现 collector：按子网范围 ICMP/TCP 探活，返回活跃 IP（含 best-effort MAC）。"""
import asyncio
import ipaddress
import json

DEFAULT_PORTS = [22, 80, 443, 3389]
CONCURRENCY = 50
MAX_CONCURRENCY = 100
MAX_TARGETS = 1024
MAX_PORTS = 32
DEFAULT_TIMEOUT = 5.0
MIN_TIMEOUT = 0.2
MAX_TIMEOUT = 30.0


class IPDiscoveryScanner:
    def __init__(self, kwargs: dict):
        self.model_id = kwargs.get("model_id", "ip")
        self.scan_method = (kwargs.get("scan_method") or "icmp").lower()
        self.ports = self._normalize_ports(kwargs.get("ports") or DEFAULT_PORTS)
        self.subnets = self._normalize_json_list(kwargs.get("subnets") or [])
        self.timeout = self._normalize_timeout(kwargs.get("timeout", DEFAULT_TIMEOUT))
        self.concurrency = self._normalize_concurrency(kwargs.get("concurrency", CONCURRENCY))
        self.targets = self._build_targets(kwargs.get("targets") or [])

    @staticmethod
    def _normalize_json_list(value):
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError):
                return []
            return parsed if isinstance(parsed, list) else []
        return value if isinstance(value, list) else []

    @classmethod
    def _normalize_ports(cls, value):
        ports = cls._normalize_json_list(value) if isinstance(value, str) else value
        if not isinstance(ports, list):
            return DEFAULT_PORTS
        normalized = []
        for port in ports:
            try:
                port_number = int(port)
            except (TypeError, ValueError):
                continue
            if 1 <= port_number <= 65535 and port_number not in normalized:
                normalized.append(port_number)
            if len(normalized) > MAX_PORTS:
                raise ValueError(f"端口数量超过上限: {MAX_PORTS}")
        return normalized or DEFAULT_PORTS

    @staticmethod
    def _normalize_timeout(value) -> float:
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT
        if timeout <= 0:
            return DEFAULT_TIMEOUT
        return min(max(timeout, MIN_TIMEOUT), MAX_TIMEOUT)

    @staticmethod
    def _normalize_concurrency(value) -> int:
        try:
            concurrency = int(value)
        except (TypeError, ValueError):
            return CONCURRENCY
        return min(max(concurrency, 1), MAX_CONCURRENCY)

    @staticmethod
    def _append_target(targets: list[dict], target: dict):
        if len(targets) >= MAX_TARGETS:
            raise ValueError(f"目标数量超过上限: {MAX_TARGETS}")
        targets.append(target)

    def _build_targets(self, explicit_targets) -> list[dict]:
        targets = []
        for ip in self._normalize_json_list(explicit_targets) if isinstance(explicit_targets, str) else explicit_targets:
            self._append_target(targets, {"ip": str(ip), "subnet_id": "", "subnet_cidr": ""})

        for subnet in self.subnets:
            if not isinstance(subnet, dict):
                continue
            cidr = str(subnet.get("cidr") or "").strip()
            try:
                network = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                continue
            reserved = {
                str(item).strip()
                for item in subnet.get("reserved_addresses", [])
                if str(item).strip()
            }
            gateway = str(subnet.get("gateway") or "").strip()
            if gateway:
                reserved.add(gateway)
            for ip in network.hosts():
                ip_text = str(ip)
                if ip_text in reserved:
                    continue
                self._append_target(
                    targets,
                    {
                        "ip": ip_text,
                        "subnet_id": str(subnet.get("subnet_id") or ""),
                        "subnet_cidr": str(network),
                    },
                )
        return targets

    async def _tcp_probe(self, ip: str, port: int, timeout: float) -> bool:
        try:
            fut = asyncio.open_connection(ip, port)
            reader, writer = await asyncio.wait_for(fut, timeout=timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    async def _tcp_alive(self, ip: str) -> bool:
        for port in self.ports:
            if await self._tcp_probe(ip, port, self.timeout):
                return True
        return False

    async def _icmp_probe(self, ip: str, timeout: float) -> bool:
        from icmplib import async_ping
        try:
            host = await async_ping(ip, count=1, timeout=timeout, privileged=True)
            return host.is_alive
        except Exception:
            return False

    def _read_mac(self, ip: str) -> str:
        """best-effort：仅同二层可得（读 ARP 表）。跨三层返回空。规格 §13.3。"""
        try:
            import subprocess
            out = subprocess.run(["arp", "-n", ip], capture_output=True, text=True, timeout=2).stdout
            for tok in out.split():
                if ":" in tok and len(tok) == 17:
                    return tok
        except Exception:
            pass
        return ""

    async def _probe_one(self, target: dict, sem: asyncio.Semaphore):
        ip = target["ip"]
        async with sem:
            alive = (await self._tcp_alive(ip)) if self.scan_method == "tcp" else (await self._icmp_probe(ip, self.timeout))
        if not alive:
            return None
        if not target.get("subnet_id"):
            return {"ip": ip, "mac": self._read_mac(ip)}
        return {
            "ip_addr": ip,
            "ip_status": "online",
            "subnet_id": target["subnet_id"],
            "subnet_cidr": target["subnet_cidr"],
            "scan_method": self.scan_method,
            "auto_collect": "true",
            "mac": self._read_mac(ip),
        }

    async def list_all_resources(self) -> dict:
        sem = asyncio.Semaphore(self.concurrency)
        results = []
        pending = set()

        for target in self.targets:
            pending.add(asyncio.create_task(self._probe_one(target, sem)))
            if len(pending) >= self.concurrency:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                results.extend(task.result() for task in done)

        if pending:
            results.extend(await asyncio.gather(*pending))

        alive = [r for r in results if r]
        return {"success": True, "result": {self.model_id: alive}}
