# The use of PyTorch in Triton programs is not allowed for the purposes of fair benchmarking.
import triton
import triton.language as tl
import numpy as np

@triton.jit
def matrix_multiplication_kernel(
    a_ptr, b_ptr, c_ptr,
    M, N, K,
    stride_am, stride_an,
    stride_bn, stride_bk,
    stride_cm, stride_ck,
    BLOCK_SIZE_M: tl.constexpr, BLOCK_SIZE_K: tl.constexpr, BLOCK_SIZE_N: tl.constexpr
):
    a_ptr = a_ptr.to(tl.pointer_type(tl.float32))
    b_ptr = b_ptr.to(tl.pointer_type(tl.float32))
    c_ptr = c_ptr.to(tl.pointer_type(tl.float32))
    pid_m = tl.program_id(axis=0)
    pid_k = tl.program_id(axis=1)

    offs_m = pid_m * BLOCK_SIZE_M + tl.arange(0, BLOCK_SIZE_M)
    offs_k = pid_k * BLOCK_SIZE_K + tl.arange(0, BLOCK_SIZE_K)
    offs_n = tl.arange(0, BLOCK_SIZE_N)

    # initialize pointers for a and b
    a_ptrs = a_ptr + offs_m[:, None] * stride_am + offs_n[None, :] * stride_an
    b_ptrs = b_ptr + offs_n[:, None] * stride_bn + offs_k[None, :] * stride_bk

    accumulator = tl.zeros((BLOCK_SIZE_M, BLOCK_SIZE_K), dtype=tl.float32)

    # accumulate along the N dimension
    for n in range(0, tl.cdiv(N, BLOCK_SIZE_N)):
        offset_n = n * BLOCK_SIZE_N
        max_idx = N - offset_n
        # 加载A和B的块
        a = tl.load(a_ptrs + offset_n * stride_an, mask=offs_n[None, :] < max_idx, other=0.0)
        b = tl.load(b_ptrs + offset_n * stride_bn, mask=offs_n[:, None] < max_idx, other=0.0)
        # 计算a @ b，累加到 accumulator
        accumulator = tl.dot(a, b, accumulator, input_precision="ieee")

    # write result back to c
    c_ptrs = c_ptr + offs_m[:, None] * stride_cm + offs_k[None, :] * stride_ck
    c_mask = (offs_m[:, None] < M) & (offs_k[None, :] < K)
    tl.store(c_ptrs, accumulator, mask=c_mask)

@triton.jit
def matmul_kernel_make_tensor_desciptor(a_ptr, b_ptr, c_ptr,  #
                                        M, N, K,  #
                                        BLOCK_SIZE_M: tl.constexpr, BLOCK_SIZE_N: tl.constexpr,
                                        BLOCK_SIZE_K: tl.constexpr,  #
                                        ):
    a_ptr = a_ptr.to(tl.pointer_type(tl.float32))
    b_ptr = b_ptr.to(tl.pointer_type(tl.float32))
    c_ptr = c_ptr.to(tl.pointer_type(tl.float32))
    pid_m = tl.program_id(axis=0)
    pid_k = tl.program_id(axis=1)

    a_desc = tl._experimental_make_tensor_descriptor(
        a_ptr,
        shape=[M, N],
        strides=[N, 1],
        block_shape=[BLOCK_SIZE_M, BLOCK_SIZE_N],
    )
    b_desc = tl._experimental_make_tensor_descriptor(
        b_ptr,
        shape=[N, K],
        strides=[K, 1],
        block_shape=[BLOCK_SIZE_N, BLOCK_SIZE_K],
    )
    c_desc = tl._experimental_make_tensor_descriptor(
        c_ptr,
        shape=[M, K],
        strides=[K, 1],
        block_shape=[BLOCK_SIZE_M, BLOCK_SIZE_K],
    )

    offset_m = pid_m * BLOCK_SIZE_M
    offset_k = pid_k * BLOCK_SIZE_K
    accumulator = tl.zeros((BLOCK_SIZE_M, BLOCK_SIZE_K), dtype=tl.float32)

    for n in range(tl.cdiv(N, BLOCK_SIZE_N)):
        offset_n = n * BLOCK_SIZE_N
        a = a_desc.load([offset_m, offset_n])
        b = b_desc.load([offset_n, offset_k])
        accumulator = tl.dot(a, b, acc=accumulator, input_precision="ieee")

    accumulator = accumulator.to(a_desc.dtype)
    c_desc.store([offset_m, offset_k], accumulator)

# a_ptr, b_ptr, c_ptr are raw device pointers
def solve(a_ptr: int, b_ptr: int, c_ptr: int, M: int, N: int, K: int):
    grid = lambda META: (triton.cdiv(M, META['BLOCK_SIZE_M']), triton.cdiv(K, META['BLOCK_SIZE_K']), )
    # Leading dimensions must be multiples of 16-byte strides
    if M % 4 == 0 and N % 4 == 0 and K % 4 == 0:
        # alloc_fn need use cudaMalloc by ctypes in LeetGPU
        import ctypes
        cudart = ctypes.CDLL("libcudart.so")
        cudart.cudaMalloc.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t]
        cudart.cudaMalloc.restype = ctypes.c_int
        # TMA descriptors require a global memory allocation
        def alloc_fn(size, alignment, stream):
            ptr = ctypes.c_void_p()
            err = cudart.cudaMalloc(ctypes.byref(ptr), size)
            if err != 0:
                raise RuntimeError(f"cudaMalloc failed, code {err}")
            return ptr.value

        triton.set_allocator(alloc_fn)
        matmul_kernel_make_tensor_desciptor[grid](
            a_ptr, b_ptr, c_ptr,
            M, N, K,
            BLOCK_SIZE_M=128,
            BLOCK_SIZE_K=64,
            BLOCK_SIZE_N=64,
        )
    else:
        matrix_multiplication_kernel[grid](
            a_ptr, b_ptr, c_ptr,
            M, N, K,
            N, 1,
            K, 1,
            K, 1,
            BLOCK_SIZE_M=128,
            BLOCK_SIZE_K=64,
            BLOCK_SIZE_N=64,
        )

M, N, K = 8192, 6144, 4096

import torch
device = torch.cuda.current_device()

A = torch.randn(M, N, device=device)
B = torch.randn(N, K, device=device)
C = torch.randn(M, K, device=device)
solve(A[None], B[None], C[None], M, N, K)
torch_output = torch.matmul(A, B)

if torch.allclose(C, torch_output, atol=0.125, rtol=0):
    print("✅ Triton and Torch match")
else:
    print("❌ Triton and Torch differ")