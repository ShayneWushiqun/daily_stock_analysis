# -*- coding: utf-8 -*-
"""
===================================
股票数据接口
===================================

职责：
1. POST /api/v1/stocks/extract-from-image 从图片提取股票代码
2. GET /api/v1/stocks/{code}/quote 实时行情接口
3. GET /api/v1/stocks/{code}/history 历史行情接口
4. GET /api/v1/stocks/watchlist 自选股列表
5. POST /api/v1/stocks/watchlist 添加自选股
6. PUT /api/v1/stocks/watchlist/{code} 更新自选股
7. DELETE /api/v1/stocks/watchlist/{code} 删除自选股
"""

import logging
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from api.v1.schemas.stocks import (
    AddStockRequest,
    ExtractFromImageResponse,
    KLineData,
    StockHistoryResponse,
    StockQuote,
    UpdateStockRequest,
    WatchlistItem,
    WatchlistResponse,
)
from api.v1.schemas.common import ErrorResponse
from src.services.image_stock_extractor import (
    ALLOWED_MIME,
    MAX_SIZE_BYTES,
    extract_stock_codes_from_image,
)
from src.services.stock_service import StockService

logger = logging.getLogger(__name__)

router = APIRouter()

# 须在 /{stock_code} 路由之前定义
ALLOWED_MIME_STR = ", ".join(ALLOWED_MIME)


@router.post(
    "/extract-from-image",
    response_model=ExtractFromImageResponse,
    responses={
        200: {"description": "提取的股票代码"},
        400: {"description": "图片无效", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="从图片提取股票代码",
    description="上传截图/图片，通过 Vision LLM 提取股票代码。支持 JPEG、PNG、WebP、GIF，最大 5MB。",
)
def extract_from_image(
    file: Optional[UploadFile] = File(None, description="图片文件（表单字段名 file）"),
    include_raw: bool = Query(False, description="是否在结果中包含原始 LLM 响应"),
) -> ExtractFromImageResponse:
    """
    从上传的图片中提取股票代码（使用 Vision LLM）。

    表单字段请使用 file 上传图片。优先级：Gemini / Anthropic / OpenAI（首个可用）。
    """
    if not file or not file.filename:
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_request", "message": "未提供文件，请使用表单字段 file 上传图片"},
        )

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_MIME:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_type",
                "message": f"不支持的类型: {content_type}。允许: {ALLOWED_MIME_STR}",
            },
        )

    try:
        # 先读取限定大小，再检查是否还有剩余（语义清晰：超出则拒绝）
        data = file.file.read(MAX_SIZE_BYTES)
        if file.file.read(1):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "file_too_large",
                    "message": f"图片超过 {MAX_SIZE_BYTES // (1024 * 1024)}MB 限制",
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"读取上传文件失败: {e}")
        raise HTTPException(
            status_code=400,
            detail={"error": "read_failed", "message": "读取上传文件失败"},
        )

    try:
        codes, raw_text = extract_stock_codes_from_image(data, content_type)
        return ExtractFromImageResponse(
            codes=codes,
            raw_text=raw_text if include_raw else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "extract_failed", "message": str(e)})
    except Exception as e:
        logger.error(f"图片提取失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "图片提取失败"},
        )


# === 自选股管理接口 ===


@router.get(
    "/watchlist",
    response_model=WatchlistResponse,
    summary="获取自选股列表",
    description="从 Supabase 获取所有自选股（含活跃和非活跃）",
)
def get_watchlist() -> WatchlistResponse:
    """获取自选股列表。"""
    from src.repositories.stock_list_repo import StockListRepository

    if not StockListRepository.is_available():
        raise HTTPException(
            status_code=503,
            detail={"error": "service_unavailable", "message": "Supabase 未配置或不可用，请检查 SUPABASE_DATABASE_URL"},
        )

    items_raw = StockListRepository.list_all()
    items = []
    for row in items_raw:
        items.append(WatchlistItem(
            id=row.get("id"),
            stock_code=row.get("stock_code", ""),
            stock_name=row.get("stock_name"),
            notes=row.get("notes"),
            is_active=row.get("is_active", True),
            created_at=str(row["created_at"]) if row.get("created_at") else None,
            updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
        ))
    return WatchlistResponse(items=items, total=len(items))


@router.post(
    "/watchlist",
    response_model=WatchlistItem,
    responses={
        200: {"description": "添加成功"},
        400: {"description": "参数错误", "model": ErrorResponse},
        503: {"description": "Supabase 不可用", "model": ErrorResponse},
    },
    summary="添加自选股",
    description="向 Supabase 自选股列表添加股票",
)
def add_watchlist_stock(request: AddStockRequest) -> WatchlistItem:
    """添加股票到自选列表。"""
    from src.repositories.stock_list_repo import StockListRepository

    if not StockListRepository.is_available():
        raise HTTPException(
            status_code=503,
            detail={"error": "service_unavailable", "message": "Supabase 未配置或不可用"},
        )

    # 自动建表
    StockListRepository.ensure_table()

    success = StockListRepository.add(
        stock_code=request.stock_code,
        stock_name=request.stock_name,
        notes=request.notes,
    )
    if not success:
        raise HTTPException(
            status_code=500,
            detail={"error": "add_failed", "message": f"添加股票 {request.stock_code} 失败"},
        )

    # 返回添加后的完整记录
    item = StockListRepository.get(request.stock_code)
    if item:
        return WatchlistItem(
            id=item.get("id"),
            stock_code=item.get("stock_code", ""),
            stock_name=item.get("stock_name"),
            notes=item.get("notes"),
            is_active=item.get("is_active", True),
            created_at=str(item["created_at"]) if item.get("created_at") else None,
            updated_at=str(item["updated_at"]) if item.get("updated_at") else None,
        )
    return WatchlistItem(stock_code=request.stock_code.upper())


@router.put(
    "/watchlist/{stock_code}",
    response_model=WatchlistItem,
    responses={
        200: {"description": "更新成功"},
        404: {"description": "股票不在自选列表", "model": ErrorResponse},
        503: {"description": "Supabase 不可用", "model": ErrorResponse},
    },
    summary="更新自选股",
    description="更新 Supabase 自选股信息（名称、备注、是否启用）",
)
def update_watchlist_stock(stock_code: str, request: UpdateStockRequest) -> WatchlistItem:
    """更新自选股信息。"""
    from src.repositories.stock_list_repo import StockListRepository

    if not StockListRepository.is_available():
        raise HTTPException(
            status_code=503,
            detail={"error": "service_unavailable", "message": "Supabase 未配置或不可用"},
        )

    success = StockListRepository.update(
        stock_code=stock_code,
        stock_name=request.stock_name,
        notes=request.notes,
        is_active=request.is_active,
    )
    if not success:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"股票 {stock_code} 不在自选列表中"},
        )

    item = StockListRepository.get(stock_code)
    if item:
        return WatchlistItem(
            id=item.get("id"),
            stock_code=item.get("stock_code", ""),
            stock_name=item.get("stock_name"),
            notes=item.get("notes"),
            is_active=item.get("is_active", True),
            created_at=str(item["created_at"]) if item.get("created_at") else None,
            updated_at=str(item["updated_at"]) if item.get("updated_at") else None,
        )
    return WatchlistItem(stock_code=stock_code.upper())


@router.delete(
    "/watchlist/{stock_code}",
    responses={
        200: {"description": "删除成功"},
        404: {"description": "股票不在自选列表", "model": ErrorResponse},
        503: {"description": "Supabase 不可用", "model": ErrorResponse},
    },
    summary="删除自选股",
    description="从 Supabase 自选列表中删除股票（物理删除）",
)
def delete_watchlist_stock(stock_code: str):
    """从自选列表删除股票。"""
    from src.repositories.stock_list_repo import StockListRepository

    if not StockListRepository.is_available():
        raise HTTPException(
            status_code=503,
            detail={"error": "service_unavailable", "message": "Supabase 未配置或不可用"},
        )

    success = StockListRepository.remove(stock_code)
    if not success:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"股票 {stock_code} 不在自选列表中"},
        )

    return {"message": f"股票 {stock_code.upper()} 已从自选删除"}


# === 行情数据接口 ===


@router.get(
    "/{stock_code}/quote",
    response_model=StockQuote,
    responses={
        200: {"description": "行情数据"},
        404: {"description": "股票不存在", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="获取股票实时行情",
    description="获取指定股票的最新行情数据"
)
def get_stock_quote(stock_code: str) -> StockQuote:
    """
    获取股票实时行情
    
    获取指定股票的最新行情数据
    
    Args:
        stock_code: 股票代码（如 600519、00700、AAPL）
        
    Returns:
        StockQuote: 实时行情数据
        
    Raises:
        HTTPException: 404 - 股票不存在
    """
    try:
        service = StockService()
        
        # 使用 def 而非 async def，FastAPI 自动在线程池中执行
        result = service.get_realtime_quote(stock_code)
        
        if result is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "not_found",
                    "message": f"未找到股票 {stock_code} 的行情数据"
                }
            )
        
        return StockQuote(
            stock_code=result.get("stock_code", stock_code),
            stock_name=result.get("stock_name"),
            current_price=result.get("current_price", 0.0),
            change=result.get("change"),
            change_percent=result.get("change_percent"),
            open=result.get("open"),
            high=result.get("high"),
            low=result.get("low"),
            prev_close=result.get("prev_close"),
            volume=result.get("volume"),
            amount=result.get("amount"),
            update_time=result.get("update_time")
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取实时行情失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"获取实时行情失败: {str(e)}"
            }
        )


@router.get(
    "/{stock_code}/history",
    response_model=StockHistoryResponse,
    responses={
        200: {"description": "历史行情数据"},
        422: {"description": "不支持的周期参数", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="获取股票历史行情",
    description="获取指定股票的历史 K 线数据"
)
def get_stock_history(
    stock_code: str,
    period: str = Query("daily", description="K 线周期", pattern="^(daily|weekly|monthly)$"),
    days: int = Query(30, ge=1, le=365, description="获取天数")
) -> StockHistoryResponse:
    """
    获取股票历史行情
    
    获取指定股票的历史 K 线数据
    
    Args:
        stock_code: 股票代码
        period: K 线周期 (daily/weekly/monthly)
        days: 获取天数
        
    Returns:
        StockHistoryResponse: 历史行情数据
    """
    try:
        service = StockService()
        
        # 使用 def 而非 async def，FastAPI 自动在线程池中执行
        result = service.get_history_data(
            stock_code=stock_code,
            period=period,
            days=days
        )
        
        # 转换为响应模型
        data = [
            KLineData(
                date=item.get("date"),
                open=item.get("open"),
                high=item.get("high"),
                low=item.get("low"),
                close=item.get("close"),
                volume=item.get("volume"),
                amount=item.get("amount"),
                change_percent=item.get("change_percent")
            )
            for item in result.get("data", [])
        ]
        
        return StockHistoryResponse(
            stock_code=stock_code,
            stock_name=result.get("stock_name"),
            period=period,
            data=data
        )
    
    except ValueError as e:
        # period 参数不支持的错误（如 weekly/monthly）
        raise HTTPException(
            status_code=422,
            detail={
                "error": "unsupported_period",
                "message": str(e)
            }
        )
    except Exception as e:
        logger.error(f"获取历史行情失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"获取历史行情失败: {str(e)}"
            }
        )

