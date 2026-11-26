"""Run via manage.py shell in the dedicated local PostgreSQL QA stack."""
import json
from datetime import timedelta
from statistics import median
from time import perf_counter

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from care import services
from care.forms import RoomForm
from care.models import Activity, Membership


def measure():
    if not settings.DEBUG or settings.APP_ORIGIN != "http://127.0.0.1:8097" or connection.vendor != "postgresql":
        raise RuntimeError("로컬 QA PostgreSQL 환경에서 실행해 주세요.")
    with transaction.atomic():
        owner, _ = get_user_model().objects.get_or_create(username="petnote-benchmark-owner")
        reader, _ = get_user_model().objects.get_or_create(username="petnote-benchmark-reader")
        room = owner.room_set.filter(name="속도 측정용").first()
        if room is None:
            form = RoomForm({"name": "속도 측정용", "species": "dog", "timezone": "Asia/Seoul", "use_defaults": "on"})
            room = services.create_room(owner, form)
            author = room.members.get(user=owner)
            Membership.objects.create(room=room, user=reader, role="guest", display_name="공동 보호자")
            now = timezone.now()
            Activity.objects.bulk_create([
                Activity(room=room, author=author, performer=author, module="walk",
                         payload={"minutes": "15", "distance": "1", "distance_unit": "km"},
                         occurred_at=now-timedelta(hours=i+1))
                for i in range(1200)
            ])
    assert room.activities.count() == 1200
    client = Client()
    client.force_login(reader)
    for suffix in ("", "modules/", "records/"):
        url = f"/rooms/{room.pk}/{suffix}"
        assert client.get(url).status_code == 200
        samples = []
        for _ in range(7):
            with CaptureQueriesContext(connection) as captured:
                start = perf_counter()
                response = client.get(url)
                samples.append(round(1000 * (perf_counter() - start), 1))
            assert response.status_code == 200
        print(json.dumps({"page": suffix or "today", "queries": len(captured),
                          "median_ms": median(samples), "samples_ms": samples}))


measure()
