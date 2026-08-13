import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.services.export_service import ExportService
from app.services.monitoring_service import MonitoringService

logger = logging.getLogger(__name__)
router = Router()

@router.callback_query(F.data == "menu_backup_db")
@router.message(Command("backup"))
async def cmd_backup(event: Message | CallbackQuery):
    if isinstance(event, CallbackQuery):
        await event.answer("Создание резервной копии...", show_alert=False)

    backup_file = await asyncio.to_thread(ExportService.create_database_backup)
    if not backup_file or not backup_file.exists():
        msg = "❌ Не удалось создать резервную копию базы данных."
        if isinstance(event, CallbackQuery):
            await event.message.answer(msg)
        else:
            await event.answer(msg)
        return

    doc = FSInputFile(str(backup_file), filename=backup_file.name)
    caption = f"💾 <b>Резервная копия базы данных SQLite:</b> <code>{backup_file.name}</code>"
    
    if isinstance(event, CallbackQuery):
        await event.message.answer_document(document=doc, caption=caption, parse_mode="HTML")
    else:
        await event.answer_document(document=doc, caption=caption, parse_mode="HTML")

@router.callback_query(F.data == "menu_scan_now")
async def cb_scan_now(call: CallbackQuery, monitoring_service: MonitoringService):
    await call.answer("⚡ Запуск немедленного сканирования всех профилей...", show_alert=True)
    
    asyncio.create_task(monitoring_service.scan_all_active_profiles(trigger="manual", force=True))
    
    await call.message.answer("⚡ <b>Сканирование всех активных профилей запущено в фоновом режиме!</b>", parse_mode="HTML")
