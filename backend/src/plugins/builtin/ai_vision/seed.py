"""插件 seed 数据：菜单 + 内置类别 + 内置模型（install 时调用，幂等）。"""
import hashlib
import json
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Menu, Role
from src.models.rbac import role_menu


async def seed_ai_vision_data(db: AsyncSession) -> None:
    """Seed 菜单 + 权限 + 绑定 admin 角色 + 内置类别 + 内置模型。幂等。"""
    await _seed_menus(db)
    await _seed_categories(db)
    await _seed_models(db)
    logger.info("[AIVision] seeded menus + categories + models")


async def _seed_menus(db: AsyncSession) -> None:
    """一期菜单树。"""
    result = await db.execute(select(Menu).where(Menu.path == "/ai-vision", Menu.parent_id == 0))
    parent = result.scalars().first()

    if not parent:
        parent = Menu(
            name="AI 视觉平台",
            parent_id=0,
            type="M",
            path="/ai-vision",
            component=None,
            permission=None,
            icon="Camera",
            sort=60,
            visible=1,
            status=1,
        )
        db.add(parent)
        await db.flush()
        logger.info("Created 'AI 视觉平台' top-level menu")

    # (name, parent_name, type, path, component, permission, icon, sort)
    menu_specs = [
        ("统计看板", "AI 视觉平台", "C", "dashboard", "ai_vision/dashboard/index", "ai_vision:dashboard:list", "DataAnalysis", 1),
        ("运行环境", "AI 视觉平台", "C", "runtime-env", "ai_vision/runtime-env/index", "ai_vision:runtime:list", "Cpu", 2),
        ("安装依赖", "运行环境", "F", None, None, "ai_vision:runtime:install", None, 1),
        ("卸载依赖", "运行环境", "F", None, None, "ai_vision:runtime:uninstall", None, 2),
        ("环境自检", "运行环境", "F", None, None, "ai_vision:runtime:test", None, 3),
        ("环境文件管理", "运行环境", "F", None, None, "ai_vision:model:manage", None, 4),
        ("摄像头管理", "AI 视觉平台", "C", "cameras", "ai_vision/cameras/index", "ai_vision:camera:list", "VideoCamera", 3),
        ("新增摄像头", "摄像头管理", "F", None, None, "ai_vision:camera:create", None, 1),
        ("编辑摄像头", "摄像头管理", "F", None, None, "ai_vision:camera:edit", None, 2),
        ("删除摄像头", "摄像头管理", "F", None, None, "ai_vision:camera:delete", None, 3),
        ("测试摄像头", "摄像头管理", "F", None, None, "ai_vision:camera:test", None, 4),
        ("类别库", "AI 视觉平台", "C", "categories", "ai_vision/categories/index", "ai_vision:category:list", "Collection", 4),
        ("新增类别", "类别库", "F", None, None, "ai_vision:category:create", None, 1),
        ("编辑类别", "类别库", "F", None, None, "ai_vision:category:edit", None, 2),
        ("删除类别", "类别库", "F", None, None, "ai_vision:category:delete", None, 3),
        ("识别事件", "AI 视觉平台", "C", "events", "ai_vision/events/index", "ai_vision:event:list", "AlarmClock", 5),
        ("新增事件", "识别事件", "F", None, None, "ai_vision:event:create", None, 1),
        ("编辑事件", "识别事件", "F", None, None, "ai_vision:event:edit", None, 2),
        ("删除事件", "识别事件", "F", None, None, "ai_vision:event:delete", None, 3),
        ("任务编排", "AI 视觉平台", "C", "tasks", "ai_vision/tasks/index", "ai_vision:task:list", "Operation", 6),
        ("新增任务", "任务编排", "F", None, None, "ai_vision:task:create", None, 1),
        ("编辑任务", "任务编排", "F", None, None, "ai_vision:task:edit", None, 2),
        ("删除任务", "任务编排", "F", None, None, "ai_vision:task:delete", None, 3),
        ("启停任务", "任务编排", "F", None, None, "ai_vision:task:control", None, 4),
        ("告警中心", "AI 视觉平台", "C", "alarms", "ai_vision/alarms/index", "ai_vision:alarm:list", "Bell", 7),
        ("处理告警", "告警中心", "F", None, None, "ai_vision:alarm:edit", None, 1),
        ("样本库", "AI 视觉平台", "C", "samples", "ai_vision/samples/index", "ai_vision:sample:list", "Picture", 8),
        ("上传样本", "样本库", "F", None, None, "ai_vision:sample:create", None, 1),
        ("删除样本", "样本库", "F", None, None, "ai_vision:sample:delete", None, 2),
    ]

    existing = list((await db.execute(select(Menu))).scalars().all())
    created_menus: list[Menu] = []

    for name, parent_name, mtype, path, component, permission, icon, sort in menu_specs:
        parent_menu = next((m for m in existing + created_menus if m.name == parent_name), None)
        if not parent_menu:
            logger.warning(f"Skip menu '{name}': parent '{parent_name}' not found")
            continue
        dup = any(m.name == name and m.parent_id == parent_menu.id for m in existing)
        if dup:
            continue
        menu = Menu(
            name=name,
            parent_id=parent_menu.id,
            type=mtype,
            path=path,
            component=component,
            permission=permission,
            icon=icon,
            sort=sort,
            visible=1,
            status=1,
        )
        db.add(menu)
        await db.flush()
        existing.append(menu)
        created_menus.append(menu)

    if created_menus:
        logger.info(f"Created {len(created_menus)} ai_vision menus")

    # 绑定 admin 角色
    admin_role = (await db.execute(select(Role).where(Role.code == "admin"))).scalar_one_or_none()
    if admin_role and created_menus:
        bound_ids = {m.id for m in admin_role.menus}
        new_bindings = [
            {"role_id": admin_role.id, "menu_id": menu.id}
            for menu in created_menus
            if menu.id not in bound_ids
        ]
        if new_bindings:
            from sqlalchemy import insert
            await db.execute(insert(role_menu), new_bindings)
            await db.flush()
            logger.info(f"Bound {len(new_bindings)} ai_vision menus to admin role")


async def _seed_categories(db: AsyncSession) -> None:
    """内置类别：人 / 车辆 / 明火 / 烟雾。幂等。"""
    from src.plugins.builtin.ai_vision.models import AIVisionCategory

    builtins = [
        {
            "code": "person",
            "name": "人员",
            "icon": "User",
            "source": "builtin",
            "coco_map": {"0": "person"},
            "description": "人体目标检测（COCO person 类）",
        },
        {
            "code": "vehicle",
            "name": "车辆",
            "icon": "Van",
            "source": "builtin",
            "coco_map": {"2": "vehicle", "5": "vehicle", "7": "vehicle"},
            "description": "车辆目标检测（COCO car/bus/truck 聚合）",
        },
        {
            "code": "fire",
            "name": "明火",
            "icon": "Orange",
            "source": "builtin",
            "coco_map": {"0": "fire"},
            "description": "明火检测（需自训练模型，内置权重评估中）",
        },
        {
            "code": "smoke",
            "name": "烟雾",
            "icon": "Cloudy",
            "source": "builtin",
            "coco_map": {"1": "smoke"},
            "description": "烟雾检测（需自训练模型）",
        },
    ]

    existing_codes = set(
        (await db.execute(select(AIVisionCategory.code))).scalars().all()
    )
    added = 0
    for item in builtins:
        if item["code"] in existing_codes:
            continue
        db.add(
            AIVisionCategory(
                code=item["code"],
                name=item["name"],
                icon=item["icon"],
                source=item["source"],
                coco_map=json.dumps(item["coco_map"], ensure_ascii=False),
                status=1,
                description=item["description"],
            )
        )
        added += 1
    if added:
        await db.flush()
        logger.info(f"Seeded {added} builtin categories")


# ---------------------------------------------------------------------------
# 内置模型注册（按 manifest.json）
# ---------------------------------------------------------------------------

_MANIFEST_PATH = (
    Path(__file__).resolve().parent / "assets" / "models" / "manifest.json"
)


def _load_manifest() -> list[dict]:
    """读取模型清单。manifest 缺失/损坏时返回空列表并告警（不阻断安装）。"""
    try:
        if not _MANIFEST_PATH.exists():
            logger.warning(f"[AIVision] manifest.json 不存在: {_MANIFEST_PATH}")
            return []
        with _MANIFEST_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("models", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[AIVision] manifest.json 解析失败: {exc}")
        return []


def _sha256_of(file_path: Path) -> str:
    """计算文件 SHA256（分块读，避免大文件占内存）。"""
    sha = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


async def _seed_models(db: AsyncSession) -> None:
    """按 manifest 注册内置模型记录。幂等：同名模型已存在则跳过。

    安装时对模型文件做 SHA256 校验：缺失/哈希不匹配会记 warning，
    但**不阻断插件安装**（模型文件可通过后续补放或离线导入补齐）。
    """
    from src.plugins.builtin.ai_vision.models import AIVisionModel

    models_dir = _MANIFEST_PATH.parent
    existing_names = set(
        (await db.execute(select(AIVisionModel.name))).scalars().all()
    )
    added = 0
    for item in _seed_model_manifest():
        name = item["name"]
        if name in existing_names:
            continue
        file_path = models_dir / item["file"]
        file_ok = True
        if not file_path.exists():
            logger.warning(f"[AIVision] 内置模型文件缺失: {file_path.name}")
            file_ok = False
        else:
            actual = _sha256_of(file_path)
            if actual.lower() != item["sha256"].lower():
                logger.warning(
                    f"[AIVision] 模型 {name} SHA256 不匹配，期望 {item['sha256'][:12]}… 实际 {actual[:12]}…"
                )
                file_ok = False
        db.add(
            AIVisionModel(
                name=name,
                file_path=f"assets/models/{file_path.name}",
                sha256=item["sha256"] if file_ok else "",
                file_size=item.get("file_size", file_path.stat().st_size if file_path.exists() else 0),
                category_map=json.dumps(item["category_map"], ensure_ascii=False),
                input_size=item.get("input_size", 640),
                quantized=item.get("quantized", False),
                source=item.get("source", "builtin"),
                license_note=item.get("license_note", ""),
                version=item.get("version", "1.0.0"),
            )
        )
        added += 1
    if added:
        await db.flush()
        logger.info(f"Seeded {added} builtin models")


_seed_model_manifest = _load_manifest  # 兼容别名（幂等读取）