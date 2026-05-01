"""企业微信消息发送 API (access_token 自动刷新)"""
import json
import time
import urllib.request


class WeChatAPI:
    def __init__(self, corp_id: str, secret: str, agent_id: int):
        self.corp_id = corp_id
        self.secret = secret
        self.agent_id = int(agent_id)
        self._token = ""
        self._exp = 0.0

    def _token_ok(self) -> str:
        if self._token and time.time() < self._exp - 60:
            return self._token
        url = (f"https://qyapi.weixin.qq.com/cgi-bin/gettoken"
               f"?corpid={self.corp_id}&corpsecret={self.secret}")
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.loads(r.read())
        if d.get("errcode", 0) != 0:
            raise RuntimeError(f"gettoken: {d}")
        self._token = d["access_token"]
        self._exp = time.time() + d.get("expires_in", 7200)
        return self._token

    def _post(self, payload: dict):
        url = (f"https://qyapi.weixin.qq.com/cgi-bin/message/send"
               f"?access_token={self._token_ok()}")
        data = json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def send(self, user_id: str, text: str):
        """发送文本消息（自动截断到4096字）"""
        text = text[:4096]
        return self._post({
            "touser": user_id,
            "msgtype": "text",
            "agentid": self.agent_id,
            "text": {"content": text},
        })
