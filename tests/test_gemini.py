"""Agnes OpenAI 兼容 API 客户端测试。"""
import importlib.util
import sys
import types
import unittest
from datetime import timezone
from pathlib import Path
from unittest.mock import Mock, patch


# 允许只运行本单元测试而不预先安装报告生成依赖。
if importlib.util.find_spec("pytz") is None:
    pytz = types.ModuleType("pytz")
    pytz.timezone = lambda _name: timezone.utc
    sys.modules["pytz"] = pytz

# 本地未创建 settings.py 时，使用示例配置加载被测模块。
if "config.settings" not in sys.modules:
    settings_path = Path(__file__).parents[1] / "config" / "settings.example.py"
    spec = importlib.util.spec_from_file_location("config.settings", settings_path)
    settings = importlib.util.module_from_spec(spec)
    sys.modules["config.settings"] = settings
    spec.loader.exec_module(settings)

from src.paper_summarizer import ModelClient


class TestAgnesClient(unittest.TestCase):
    @patch("src.paper_summarizer.requests.post")
    def test_chat_completion_uses_openai_compatible_format(self, mock_post):
        api_response = {
            "choices": [{
                "message": {"role": "assistant", "content": "测试成功"},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
            },
        }
        mock_response = Mock(status_code=200)
        mock_response.json.return_value = api_response
        mock_post.return_value = mock_response

        client = ModelClient("test-api-key")
        messages = [
            {"role": "system", "content": "请使用中文回答。"},
            {"role": "user", "content": "你好"},
        ]
        result = client.chat_completion(
            messages,
            temperature=0,
            max_tokens=128,
        )

        self.assertEqual(result, api_response)
        mock_post.assert_called_once_with(
            "https://api.agnes-ai.cn/v1/chat/completions",
            headers={
                "Authorization": "Bearer test-api-key",
                "Content-Type": "application/json",
            },
            json={
                "model": "agnes-2.5-flash",
                "messages": messages,
                "temperature": 0,
                "top_p": 0.8,
                "max_tokens": 128,
            },
            timeout=300,
        )

    def test_empty_messages_are_rejected(self):
        client = ModelClient("test-api-key")
        with self.assertRaisesRegex(ValueError, "messages 不能为空"):
            client.chat_completion([])


if __name__ == "__main__":
    unittest.main()
