"""Cloud deployment entry using Huawei's official AgentArts Runtime SDK.

Optional SDK is intentionally outside local core dependencies. Install this
directory's requirements before deploying this entry point.
"""
from agentarts.sdk import AgentArtsRuntimeApp

from testfix.agentarts_runtime import handle_invocation

app = AgentArtsRuntimeApp()


@app.entrypoint
def invoke(payload: dict):
    return handle_invocation(payload)


if __name__ == "__main__":
    app.run()

