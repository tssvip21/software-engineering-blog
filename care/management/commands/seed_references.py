from datetime import date
from django.core.management.base import BaseCommand
from care.models import Reference

class Command(BaseCommand):
    help = "출처와 적용 조건을 확인한 BCS 참고 항목만 등록합니다."
    def handle(self, *args, **options):
        for species, low, high, filename, extra in (
            ("dog", 4, 5, "WSAVA_BCSCat_BCSDog_Nutrition_250612.pdf", "개 차트의 9점 척도 4–5 구간입니다."),
            ("cat", 5, 5, "WSAVA_BCSCat_BCSCat_Nutrition_250612.pdf", "고양이 차트는 5를 기준으로 제시하며, 일부 고양이 특히 노령묘에서는 6도 허용될 수 있다고 명시합니다."),
        ):
            Reference.objects.update_or_create(key="wsava-bcs-" + species, defaults={
                "species": species, "size": "", "module": "weight", "metric": "bcs", "label": "기록한 BCS의 참고 구간", "low": low, "high": high,
                "unit": "/ 9", "source_name": "WSAVA Global Nutrition Committee · Body Condition Score (2025-06)",
                "source_url": "https://wsava.org/wp-content/uploads/2025/06/" + filename,
                "conditions": extra + " 입력된 BCS 관찰·평가값과 비교할 뿐 체중이나 체격에서 산출하지 않습니다. 성장기·임신·질환 등 개별 조건의 적합성은 수의사 확인이 필요하며, 이 표시는 질병 진단이나 자동 식이 처방이 아닙니다.",
                "checked_on": date(2026, 11, 3),
            })
        self.stdout.write("Verified reference rows: 2")
