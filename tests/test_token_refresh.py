"""Token refresh single-flight and cooldown behaviour tests.

These mirror the official app's TokenLoader: concurrent refresh attempts share
one in-flight request, results are reused for a 20 s cooldown window, and an
expired refreshToken (10002) propagates a re-auth failure without caching.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import sys
import types
import unittest

from tests.test_platform_setup import install_home_assistant_stubs


INTEGRATION_PATH = (
    Path(__file__).parents[1] / "custom_components" / "leelen_home3"
)


def load_http_api():
    install_home_assistant_stubs()
    package_name = "token_probe"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(INTEGRATION_PATH)]
        sys.modules[package_name] = package
        leelen = types.ModuleType(f"{package_name}.leelen")
        leelen.__path__ = [str(INTEGRATION_PATH / "leelen")]
        sys.modules[leelen.__name__] = leelen
        api_pkg = types.ModuleType(f"{package_name}.leelen.api")
        api_pkg.__path__ = [str(INTEGRATION_PATH / "leelen" / "api")]
        sys.modules[api_pkg.__name__] = api_pkg
    return importlib.import_module(f"{package_name}.leelen.api.HttpApi")


class TokenRefreshTests(unittest.TestCase):
    def _make_api(self, calls, fail_with=None):
        http_api = load_http_api()
        api = http_api.HttpApi(None)
        api._refresh_token = "rt"

        async def fake_request():
            calls.append(1)
            # 模拟网络延迟，制造协程交错窗口
            await asyncio.sleep(0.01)
            if fail_with is not None:
                raise fail_with
            return True

        api._refresh_token_request = fake_request
        return api, http_api

    def test_concurrent_refreshes_share_single_request(self):
        calls = []
        api, _ = self._make_api(calls)

        async def main():
            return await asyncio.gather(
                api._do_refresh_token(),
                api._do_refresh_token(),
                api._do_refresh_token(),
            )

        results = asyncio.run(main())
        self.assertEqual([True, True, True], list(results))
        self.assertEqual(1, len(calls))

    def test_cooldown_window_suppresses_repeat_refresh(self):
        calls = []
        api, _ = self._make_api(calls)

        async def main():
            first = await api._do_refresh_token()
            second = await api._do_refresh_token()
            return first, second

        first, second = asyncio.run(main())
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(1, len(calls))

    def test_cooldown_expiry_allows_new_refresh(self):
        calls = []
        api, _ = self._make_api(calls)

        async def main():
            import time

            await api._do_refresh_token()
            # 把冷却窗口拨到过去
            api._refresh_result_cache = (time.monotonic() - 1, True)
            return await api._do_refresh_token()

        asyncio.run(main())
        self.assertEqual(2, len(calls))

    def test_auth_failure_is_not_cached(self):
        calls = []
        api, http_api = self._make_api(
            calls, fail_with=RuntimeError("placeholder")
        )
        # 用真实的 ConfigEntryAuthFailed 触发"不缓存"路径
        failure = http_api.ConfigEntryAuthFailed("refreshToken 已过期")

        async def fake_request():
            calls.append(1)
            raise failure

        api._refresh_token_request = fake_request

        async def main():
            with self.assertRaises(http_api.ConfigEntryAuthFailed):
                await api._do_refresh_token()
            with self.assertRaises(http_api.ConfigEntryAuthFailed):
                await api._do_refresh_token()

        asyncio.run(main())
        self.assertEqual(2, len(calls))

    def test_generic_failure_is_cached_within_cooldown(self):
        calls = []
        api, _ = self._make_api(calls, fail_with=RuntimeError("network"))

        async def main():
            first = await api._do_refresh_token()
            second = await api._do_refresh_token()
            return first, second

        first, second = asyncio.run(main())
        self.assertFalse(first)
        self.assertFalse(second)
        self.assertEqual(1, len(calls))

    def test_ensure_terminal_id_reuses_stored_identity(self):
        api, _ = self._make_api([])
        self.assertEqual(
            "ANDROID-persisted", api.ensure_terminal_id("ANDROID-persisted")
        )
        self.assertEqual(
            "ANDROID-persisted",
            api.ensure_terminal_id(""),
        )
        self.assertEqual(
            "ANDROID-persisted",
            api.ensure_terminal_id(None),
        )


if __name__ == "__main__":
    unittest.main()
