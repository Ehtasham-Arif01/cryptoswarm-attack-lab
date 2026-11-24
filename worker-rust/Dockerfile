# Build a static (musl) binary, then ship it alone in an empty "scratch" image:
# no OS, no shell, no package manager, so there's almost nothing for an attacker (or Trivy) to find.
FROM rust:1-alpine AS build
RUN apk add --no-cache musl-dev
WORKDIR /src
# Dependencies first, so they're cached between builds
COPY worker-rust/Cargo.toml worker-rust/Cargo.lock ./
RUN mkdir src && echo "fn main() {}" > src/main.rs && cargo build --release --locked && rm -rf src
COPY worker-rust/src ./src
RUN touch src/main.rs && cargo test --release --locked && cargo build --release --locked

FROM scratch
COPY --from=build /src/target/release/cryptoswarm-worker /cryptoswarm-worker
USER 10001:10001
ENTRYPOINT ["/cryptoswarm-worker"]
