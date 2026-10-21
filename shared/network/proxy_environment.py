HTTP_PROXY_KEYS = ("HTTP_PROXY", "http_proxy")
HTTPS_PROXY_KEYS = ("HTTPS_PROXY", "https_proxy")
NO_PROXY_KEYS = ("NO_PROXY", "no_proxy")


class ProxyEnvBuilder:
    """
    代理环境变量构建器。

    根据代理配置（http/https/no_proxy + 用户名密码）与当前环境变量，
    构建一个可 ``os.environ.update(...)`` 的覆盖层 ``overlay``（dict），
    以及需要事后清理/恢复的键集合 ``cleanup_keys``（set）。

    同一大小的两个变体（如 ``HTTP_PROXY`` 与 ``http_proxy``）都会被设置，
    因为不同 HTTP 库读取的大小写约定不同。
    """

    @staticmethod
    def build_overlay(
        *,
        use_proxy: bool,
        http_proxy: str | None = None,
        https_proxy: str | None = None,
        proxy_username: str | None = None,
        proxy_password: str | None = None,
        no_proxy: str | None = None,
        current_env=None,
    ) -> "tuple[dict[str, str], set[str]]":
        """
        构建代理环境变量覆盖层。

        ``use_proxy=False`` 时返回空 overlay（不设置任何代理变量）。
        ``use_proxy=True`` 时按 http/https/no_proxy 分别构造带认证的代理 URL，
        同时写入大小写两个变体。``proxy_username``/``proxy_password`` 以
        ``user:pass@`` 形式嵌入 URL；空值不嵌入。

        ``current_env``（可选，默认 ``os.environ``）用于判断哪些键当前不存在，
        以便 ``cleanup_keys`` 区分"新增键（事后删除）"与"已有键（事后恢复原值）"。
        简化实现里 ``cleanup_keys`` 统一返回所有被覆盖的键，供调用方恢复用。
        """
        import os
        from urllib.parse import quote

        overlay: dict[str, str] = {}
        cleanup_keys: set[str] = set()

        if not use_proxy:
            return overlay, cleanup_keys

        env = current_env if current_env is not None else os.environ

        def _with_auth(proxy_url: str) -> str:
            """在 proxy_url 中嵌入 user:pass@（已有 @ 则不重复嵌入）。"""
            if not proxy_url:
                return proxy_url
            if "@" in proxy_url:
                # 已含认证信息，原样返回
                return proxy_url
            if not proxy_username or not proxy_password:
                return proxy_url
            user = quote(proxy_username, safe="")
            pwd = quote(proxy_password, safe="")
            if "://" in proxy_url:
                scheme, rest = proxy_url.split("://", 1)
                return f"{scheme}://{user}:{pwd}@{rest}"
            # 无 scheme，默认 http
            return f"http://{user}:{pwd}@{proxy_url}"

        def _set(keys: "tuple[str, ...]", value: str) -> None:
            for key in keys:
                if key not in env:  # 仅记录原本不存在的键为"需清理"
                    pass  # cleanup_keys 在下方统一记录
                overlay[key] = value
                cleanup_keys.add(key)

        if http_proxy:
            _set(HTTP_PROXY_KEYS, _with_auth(http_proxy))
        if https_proxy:
            _set(HTTPS_PROXY_KEYS, _with_auth(https_proxy))
        if no_proxy:
            _set(NO_PROXY_KEYS, no_proxy)

        return overlay, cleanup_keys

    @staticmethod
    def restore_env(overlay: "dict[str, str]", cleanup_keys: "set[str]", original_env=None) -> None:
        """
        恢复环境变量（``build_overlay`` 的逆操作）。

        - ``cleanup_keys`` 中、``original_env`` 里原本不存在的键 → 从 ``os.environ`` 删除；
        - ``original_env`` 里原本存在的键 → 恢复原值。

        调用方在代理使用完毕后调用，确保不污染全局 ``os.environ``。
        """
        import os

        env = original_env if original_env is not None else os.environ
        for key in cleanup_keys:
            if key in env:
                os.environ[key] = env[key]
            else:
                os.environ.pop(key, None)
