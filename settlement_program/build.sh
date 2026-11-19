#!/usr/bin/env bash
set -euo pipefail

program_dir=$(cd "$(dirname "$0")" && pwd)
tools_dir=${SOLANA_PLATFORM_TOOLS:?Set SOLANA_PLATFORM_TOOLS to the extracted Anza v1.58 platform-tools directory}
target_dir=${CARGO_TARGET_DIR:-"$program_dir/target"}
export PATH="$tools_dir/llvm/bin:$PATH"
export RUSTC="$tools_dir/rust/bin/rustc"
export CC_sbpf_solana_solana="$tools_dir/llvm/bin/clang"
export AR_sbpf_solana_solana="$tools_dir/llvm/bin/llvm-ar"
"$tools_dir/rust/bin/cargo" build --manifest-path "$program_dir/Cargo.toml" --locked --release --target sbpf-solana-solana --target-dir "$target_dir"
"$tools_dir/llvm/bin/llvm-objcopy" --strip-all "$target_dir/sbpf-solana-solana/release/freightos_settlement.so" "$program_dir/../freightos/artifacts/reserve_fork/freightos_settlement.so"
