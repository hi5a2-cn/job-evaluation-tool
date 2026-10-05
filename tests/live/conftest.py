import os

# 真实环境测试（联网、需要 API Key）必须显式开启：JET_LIVE=1（原则 VIII）。
# 只忽略本目录；不要在这里用 pytest_collection_modifyitems，它会作用到全部测试。
if os.environ.get("JET_LIVE") != "1":
    collect_ignore_glob = ["*"]
