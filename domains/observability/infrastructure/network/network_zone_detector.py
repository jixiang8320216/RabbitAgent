"""网络区域探测。

判定当前进程处于哪个网络区域（本地 / 内网 / 云上 / 未知），
作为遥测的一个维度。同一个部署的不同环境打上不同标签，
便于在监控后端按区域筛选与告警。

判定依据是本机 IP 地址所属网段 —— 不发任何网络请求，
因此不会给启动路径增加延迟或外部依赖。

**历史缺口**：本模块此前是空文件，``composition`` 调用
``detect_network_zone()`` 与 ``NetworkZone.UNKNOWN`` 时抛 AttributeError，
而这正好在遥测初始化的异常兜底路径上。
"""

from __future__ import annotations

import enum
import ipaddress
import socket


class NetworkZone(enum.StrEnum):
    """网络区域分类。"""

    #: 环回地址
    LOOPBACK = "loopback"
    #: 私有网段（10/8、172.16/12、192.168/16）
    PRIVATE = "private"
    #: 公网地址
    PUBLIC = "public"
    #: 探测失败
    UNKNOWN = "unknown"


def _local_ip_addresses() -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """取本机所有网络接口的 IP。"""
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            raw = info[4][0]
            try:
                addresses.append(ipaddress.ip_address(raw))
            except ValueError:
                continue
    except (OSError, socket.gaierror):
        return []
    return addresses


def detect_network_zone() -> NetworkZone:
    """探测当前进程所处的网络区域。

    判定顺序：

    1. 全部地址都是环回 → ``LOOPBACK``
    2. 存在私有地址 → ``PRIVATE``
    3. 存在公网地址 → ``PUBLIC``
    4. 探测不到地址 → ``UNKNOWN``
    """
    addresses = _local_ip_addresses()
    if not addresses:
        return NetworkZone.UNKNOWN

    if all(address.is_loopback for address in addresses):
        return NetworkZone.LOOPBACK
    if any(address.is_private for address in addresses):
        return NetworkZone.PRIVATE
    return NetworkZone.PUBLIC


__all__ = ["NetworkZone", "detect_network_zone"]
