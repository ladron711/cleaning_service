from django.contrib import messages
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import ApplicationForm
from .models import Application, Equipment, RoadSection


@require_http_methods(["GET", "POST"])
def home(request):
    form = ApplicationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            equipment = Equipment.objects.select_for_update().get(pk=form.cleaned_data["equipment"].pk)
            if equipment.status == Equipment.Status.MAINTENANCE:
                form.add_error("equipment", "Погрузчик на техническом обслуживании.")
            else:
                application = form.save(commit=False)
                application.status = Application.Status.WAITING
                application.save()
                messages.success(request, f"Заявка №{application.pk} принята. Статус: В ожидании.")
                return redirect("home")

    equipment = Equipment.objects.prefetch_related(
        Prefetch(
            "application_set",
            queryset=Application.objects.filter(status=Application.Status.IN_PROGRESS)
            .select_related("section__direction"),
            to_attr="current_work",
        )
    ).order_by("pk")
    sections = list(RoadSection.objects.values("id", "direction_id"))
    search_requested = "number" in request.GET
    number = request.GET.get("number", "").strip()
    found_application = None
    # Некорректный или слишком большой номер считаем неизвестным.
    if number.isascii() and number.isdecimal() and len(number) <= 19:
        if 0 < int(number) <= 9223372036854775807:
            found_application = Application.objects.select_related(
                "section", "equipment"
            ).filter(pk=int(number)).first()
    return render(request, "main/home.html", {
        "all_applications": Application.objects.select_related("section").only(
            "id", "status", "status_changed_at", "section__start_point", "section__end_point"
        ).order_by("-status_changed_at", "-pk"),
        "form": form, "equipment_list": equipment, "sections": sections,
        "search_requested": search_requested, "search_number": number,
        "found_application": found_application,
    })
