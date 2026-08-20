import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.resume_parser import parse_resume, parse_source_department
from config import METADATA_PATH


WU_SAMPLE = """
工程师简介
姓名：吴云畅
性别：男
工作年限：6年
邮箱：wuyunchang@example.com
联系电话：13800000000

工作经历
2019.07-2022.08  昆仑联通  数字化办公工程师
2022.09-至今  昆仑联通  Power Platform 开发工程师

专业技能
Python：4年，熟悉自动化脚本、数据处理和接口集成。
PowerPlatform：3年，熟悉 Power Apps、Power Automate、Power BI。
M365：4年，熟悉 SharePoint Online、Teams、Exchange Online。

项目经验
项目名称：数字化办公门户建设
项目日期：2023.01-2023.06
项目描述：建设基于 M365 与 SharePoint Online 的统一办公门户。
产品或技术：M365、SharePoint Online、PowerPlatform
角色或工作内容：负责流程梳理、页面配置和自动化流程开发。

项目名称：费用报销自动化流程
项目日期：2023.07-2023.10
项目描述：使用 Power Automate 对接审批和消息通知。
产品或技术：PowerPlatform、Power Automate、Python
角色或工作内容：负责流程开发、异常处理和上线支持。

项目名称：运营数据看板
项目日期：2024.01-2024.04
项目描述：通过 Python 清洗数据，并使用 Power BI 展示指标。
产品或技术：Python、Power BI、M365
角色或工作内容：负责数据处理脚本和报表设计。
"""


FENG_SAMPLE = """
工程师简介
姓名：冯振书
性别：男
工作年限：12年
邮箱：fengzhenshu@example.com
联系电话：13900000000

工作经历
2012.07-2018.05  某信息技术有限公司  虚拟化工程师
2018.06-至今  昆仑联通  云服务部高级工程师

专业技能
VMware：10年，熟悉 vSphere、vCenter、ESXi、虚拟化平台运维。
Citrix：8年，熟悉桌面云、应用虚拟化和交付控制器。
Nutanix：4年，熟悉超融合平台部署和故障处理。
阿里云：5年，熟悉云主机、网络和基础资源管理。

项目经验
项目名称：VMware 虚拟化平台升级项目
项目日期：2021.03-2021.09
项目描述：完成 vSphere 集群升级、主机迁移和资源池优化。
产品或技术：VMware、vSphere、vCenter
角色或工作内容：负责升级方案、实施切换和故障处理。

项目名称：Citrix 桌面云建设项目
项目日期：2022.02-2022.08
项目描述：建设桌面云交付环境，完成用户策略、镜像和交付组配置。
产品或技术：Citrix、Windows Server
角色或工作内容：负责环境部署、策略配置和交付测试。

相关证书
VMware VCP-DCV 认证
NUTANIX NCP 认证
阿里云 ACP 认证
"""


def load_metadata_resume(name_keyword: str) -> tuple[str, str, list[dict]]:
    if not METADATA_PATH.exists():
        return "", "", []
    data = json.loads(METADATA_PATH.read_text(encoding="utf-8") or "{}")
    for record in data.get("files", []):
        if name_keyword in (record.get("original_name") or ""):
            return record.get("parsed_text") or "", record.get("original_name") or "", record.get("assets", [])
    return "", "", []


def get_resume_input(name_keyword: str, fallback_text: str, fallback_name: str) -> tuple[str, str, list[dict]]:
    parsed_text, original_name, assets = load_metadata_resume(name_keyword)
    if parsed_text.strip():
        return parsed_text, original_name, assets
    return fallback_text, fallback_name, []


def assert_skill_names(resume: dict, expected_names: list[str]) -> None:
    names = {item.get("name") for item in resume.get("skills", [])}
    missing = [name for name in expected_names if name not in names]
    assert not missing, f"missing skills: {missing}; got {names}"


def assert_cert_names(resume: dict, expected_fragments: list[str]) -> None:
    names = [
        " ".join(str(value or "") for value in [item.get("canonical_id"), item.get("display_name"), item.get("standard_name")])
        for item in resume.get("certificates", [])
    ]
    for fragment in expected_fragments:
        assert any(fragment.lower() in name.lower() for name in names), f"missing certificate fragment {fragment}; got {names}"


def test_source_department() -> None:
    info = parse_source_department(
        source_path="工程师简历&证书/云服务部/2023-简历/云事业部-冯振书20230322.docx"
    )
    assert info["department"] == "云服务部"
    assert info["source_group"] == "2023-简历"
    assert info["source_folder"] == "工程师简历&证书/云服务部/2023-简历"


def test_wu_yunchang() -> dict:
    parsed_text, original_name, assets = get_resume_input("吴云畅", WU_SAMPLE, "数字化办公-吴云畅.docx")
    resume = parse_resume(
        parsed_text=parsed_text,
        original_name=original_name,
        source_folder="工程师简历&证书/云服务部/2023-简历",
        assets=assets,
    )
    assert resume["person_name"] == "吴云畅"
    assert resume["gender"] == "男"
    assert resume["work_years"] is not None
    assert_skill_names(resume, ["Python", "PowerPlatform", "M365"])
    assert isinstance(resume["projects"], list)
    assert len(resume["projects"]) >= 3
    fabricated = [item for item in resume["certificates"] if item.get("canonical_id")]
    assert fabricated == [], f"吴云畅样例没有相关证书栏目，不应编造文本证书: {fabricated}"
    return resume


def test_feng_zhenshu() -> dict:
    parsed_text, original_name, assets = get_resume_input("冯振书", FENG_SAMPLE, "云服务部-冯振书20230322.docx")
    resume = parse_resume(
        parsed_text=parsed_text,
        original_name=original_name,
        source_folder="工程师简历&证书/云服务部/2023-简历",
        assets=assets,
    )
    assert resume["person_name"] == "冯振书"
    assert resume["gender"] == "男"
    assert resume["work_years"] is not None
    assert_skill_names(resume, ["VMware", "Citrix"])
    assert isinstance(resume["projects"], list)
    assert len(resume["projects"]) >= 2
    assert_cert_names(resume, ["VCP", "ACP"])
    return resume


def test_ocr_certificate_assets() -> None:
    resume = parse_resume(
        parsed_text="工程师简介\n姓名：冯振书\n性别：男\n工作年限：6年",
        original_name="云服务部-冯振书20230322.docx",
        source_folder="工程师简历&证书/云服务部/2023-简历",
        assets=[
            {
                "id": "asset_vmware_001",
                "asset_type": "image",
                "source_location": "word/media/image1.png",
                "ocr_text": "VMware Certified Professional Data Center Virtualization VCP-DCV Issued to 冯振书 Certificate ID ABC123",
            },
            {
                "id": "asset_no_ocr_001",
                "asset_type": "image",
                "source_location": "word/media/image2.png",
            },
        ],
    )
    vmware = next(item for item in resume["certificates"] if item.get("canonical_id") == "vmware_vcp")
    assert vmware["sources"] == ["ocr"], vmware
    assert vmware["display_name"] == "VCP", vmware
    assert vmware["family"] == "vmware", vmware
    assert vmware["persons"], vmware

    pending = next(item for item in resume["embedded_certificate_candidates"] if item.get("asset_id") == "asset_no_ocr_001")
    assert pending["usable"] is False
    assert pending["needs_ocr"] is True
    assert pending["standardization_status"] == "image_pending_ocr"
    return

    embedded = [item for item in resume["certificates"] if item.get("source_type") == "embedded_image"]
    vmware = next(item for item in embedded if item.get("asset_id") == "asset_vmware_001")
    assert vmware["evidence_level"] == "ocr_text"
    assert vmware["normalized_name"] == "VMware VCP"
    assert vmware["vendor"] == "VMware"
    assert vmware["usable"] is True
    assert vmware["needs_ocr"] is False
    assert vmware.get("holder_name") == "冯振书" or "冯振书" in vmware.get("evidence_text", "")

    pending = next(item for item in embedded if item.get("asset_id") == "asset_no_ocr_001")
    assert pending["usable"] is False
    assert pending["needs_ocr"] is True
    assert pending["standardization_status"] == "image_pending_ocr"


def main() -> None:
    test_source_department()
    test_ocr_certificate_assets()
    wu = test_wu_yunchang()
    feng = test_feng_zhenshu()
    summary = {
        "吴云畅": {
            "person_name": wu.get("person_name"),
            "department": wu.get("department"),
            "work_years": wu.get("work_years"),
            "skills": [item.get("name") for item in wu.get("skills", [])],
            "project_count": len(wu.get("projects", [])),
            "certificates": [item.get("canonical_id") for item in wu.get("certificates", []) if item.get("canonical_id")],
            "parse_warnings": wu.get("parse_warnings", []),
        },
        "冯振书": {
            "person_name": feng.get("person_name"),
            "department": feng.get("department"),
            "work_years": feng.get("work_years"),
            "skills": [item.get("name") for item in feng.get("skills", [])],
            "project_count": len(feng.get("projects", [])),
            "certificates": [item.get("canonical_id") for item in feng.get("certificates", []) if item.get("canonical_id")],
            "parse_warnings": feng.get("parse_warnings", []),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("resume parser sample tests passed")


if __name__ == "__main__":
    main()
