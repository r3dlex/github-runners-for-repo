# Based on https://github.com/actions/runner-images
# Containerized GitHub Actions self-hosted runner
FROM ubuntu:22.04

ARG RUNNER_VERSION=2.321.0
ARG TARGETARCH=x64

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    curl \
    jq \
    build-essential \
    libssl-dev \
    libffi-dev \
    python3 \
    python3-venv \
    python3-dev \
    python3-pip \
    git \
    sudo \
    libicu70 \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user for the runner
RUN useradd -m docker && \
    echo "docker ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

WORKDIR /home/docker/actions-runner

# Download and extract the runner
RUN curl -o actions-runner-linux-${TARGETARCH}-${RUNNER_VERSION}.tar.gz -L \
    https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${TARGETARCH}-${RUNNER_VERSION}.tar.gz \
    && tar xzf actions-runner-linux-${TARGETARCH}-${RUNNER_VERSION}.tar.gz \
    && rm actions-runner-linux-${TARGETARCH}-${RUNNER_VERSION}.tar.gz

# Install runner dependencies
RUN ./bin/installdependencies.sh

# Set ownership to the docker user
RUN chown -R docker:docker /home/docker/actions-runner

COPY start.sh start.sh
RUN chmod +x start.sh

USER docker

ENTRYPOINT ["./start.sh"]
