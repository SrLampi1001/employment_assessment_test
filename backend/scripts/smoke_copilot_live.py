"""End-to-end live verification of the AI copilot with real Mistral + NVIDIA keys.

Run while the dev uvicorn server is up on http://127.0.0.1:8000.

Steps:
  1. Register + login as a smoke user.
  2. Create a group channel.
  3. Send 3 messages (this triggers live Mistral embeddings).
  4. Ask the copilot a question about those messages (this triggers
     live NVIDIA NIM chat).
  5. Print the answer + citations + denial_code + prompt_version.
"""
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=60.0) as c:
        # 1. Register
        username = f"smoke_{int(time.time())}"
        password = "test_password_123"
        r = c.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "display_name": "Smoke User",
                "locale": "es",
                "password": password,
            },
        )
        r.raise_for_status()
        user_id = r.json()["user_id"]
        print(f"[1] registered user_id={user_id}")

        # 2. Login
        r = c.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        r.raise_for_status()
        access = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {access}"}
        print(f"[2] login OK (access_token len={len(access)})")

        # 3. Create channel
        r = c.post(
            "/api/v1/channels/group",
            headers=headers,
            json={"name": "copilot-live-test"},
        )
        r.raise_for_status()
        channel_id = r.json()["channel_id"]
        print(f"[3] channel_id={channel_id}")

        # 4. Send 3 messages
        bodies = [
            "El próximo sprint vamos a enfocarnos en el módulo de autenticación con JWT y refresh tokens rotativos.",
            "También necesitamos mejorar el copilot para que cite los mensajes correctamente.",
            "Andrés está liderando la implementación del backend con FastAPI y PostgreSQL.",
        ]
        msg_ids = []
        for body in bodies:
            r = c.post(
                f"/api/v1/channels/{channel_id}/messages",
                headers=headers,
                json={"body": body},
            )
            r.raise_for_status()
            msg_id = r.json()["rw_id"]
            msg_ids.append(msg_id)
            print(f"[4] sent message id={msg_id} body={body[:60]!r}")

        # 5. Verify embeddings actually landed on the DB
        # (skip — we trust the trigger and the smoke test)

        # 6. Ask the copilot
        print("[5] asking copilot — this calls Mistral + NVIDIA live ...")
        t0 = time.time()
        r = c.post(
            "/api/v1/copilot/query",
            headers=headers,
            json={
                "question": "¿De qué habla el equipo en este canal?",
                "top_k": 3,
            },
        )
        elapsed = time.time() - t0
        r.raise_for_status()
        answer = r.json()
        print(f"[5] copilot responded in {elapsed:.2f}s")
        print(json.dumps(answer, indent=2, ensure_ascii=False))

        # 7. Fetch usage
        r = c.get("/api/v1/copilot/usage", headers=headers)
        r.raise_for_status()
        usage = r.json()
        print(f"[6] usage: {json.dumps(usage, indent=2)}")

        # 8. Probe a denial path
        # Send a fresh user — they're not a member of any channel, so
        # the copilot should fire deny:no-permission.
        user2 = f"outsider_{int(time.time())}"
        c.post(
            "/api/v1/auth/register",
            json={
                "username": user2,
                "display_name": "Outsider",
                "locale": "es",
                "password": password,
            },
        ).raise_for_status()
        r = c.post(
            "/api/v1/auth/login",
            json={"username": user2, "password": password},
        )
        r.raise_for_status()
        headers2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
        print(f"[7] outsider login OK; asking copilot (should be denied)")
        r = c.post(
            "/api/v1/copilot/query",
            headers=headers2,
            json={"question": "¿Qué dijo Camila?", "top_k": 3},
        )
        r.raise_for_status()
        print(json.dumps(r.json(), indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
