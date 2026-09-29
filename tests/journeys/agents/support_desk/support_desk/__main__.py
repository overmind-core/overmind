import sys

from openai import OpenAI
from overmind.config import load

import overmind
from support_desk.agent import CAPABILITY_IDS, handle


def main(tickets: list[str]) -> None:
    CAPABILITY_IDS.update({slug: cap.id for slug, cap in load().capabilities.items() if cap.id})
    overmind.init(service_name="support-desk", providers=["openai"])
    client = OpenAI()
    for ticket in tickets:
        print(handle(client, ticket))
    overmind.force_flush_traces(timeout_millis=10_000)


if __name__ == "__main__":
    main(sys.argv[1:])
