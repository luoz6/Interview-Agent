FROM python:3.11-slim-bookworm AS python_runtime

FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG NODE_VERSION=22.21.0
ARG NODE_SHA256=71a04f4b9144870c9407b8019fe912514229e50246bc706862eded3ac8e9025d

COPY --from=python_runtime /usr/local /usr/local

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        libbz2-1.0 \
        libexpat1 \
        libffi8 \
        libgdbm6t64 \
        liblzma5 \
        libncursesw6 \
        libreadline8t64 \
        libsqlite3-0 \
        libuuid1 \
        postgresql-client-16 \
        xz-utils \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

RUN curl --fail --location --silent --show-error \
        "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
        --output /tmp/node.tar.xz \
    && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum --check --strict \
    && mkdir -p /opt/node \
    && tar --extract --xz --file /tmp/node.tar.xz \
        --strip-components=1 --directory /opt/node \
    && rm /tmp/node.tar.xz

ENV PATH="/opt/node/bin:${PATH}"

RUN case "$(python3.11 --version)" in Python\ 3.11.*) ;; *) exit 1 ;; esac \
    && test "$(node --version)" = "v22.21.0" \
    && test "$(npm --version)" = "10.9.4"

WORKDIR /work
