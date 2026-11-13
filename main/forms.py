from django import forms

from .models import Application, Direction, Equipment, RoadSection


class EquipmentRadioSelect(forms.RadioSelect):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value and value.instance.status == Equipment.Status.MAINTENANCE:
            option["attrs"]["disabled"] = True
            option["attrs"].pop("checked", None)
        return option


class ApplicationForm(forms.ModelForm):
    direction = forms.ModelChoiceField(
        queryset=Direction.objects.all(), label="Направление", widget=forms.RadioSelect, empty_label=None
    )

    class Meta:
        model = Application
        fields = ("direction", "section", "equipment", "urgency", "customer_name", "phone")
        labels = {
            "section": "Участок", "equipment": "Погрузчик", "urgency": "Срочность",
            "customer_name": "Имя", "phone": "Телефон",
        }
        widgets = {
            "section": forms.RadioSelect,
            "equipment": EquipmentRadioSelect,
            "urgency": forms.RadioSelect,
            "customer_name": forms.TextInput(attrs={"autocomplete": "name", "placeholder": "имя"}),
            "phone": forms.TextInput(attrs={"type": "tel", "autocomplete": "tel", "placeholder": "+7-------"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["direction"].label_from_instance = lambda obj: obj.name
        self.fields["section"].queryset = RoadSection.objects.all()
        self.fields["section"].label_from_instance = (
            lambda obj: f"{obj.start_point} — {obj.end_point}"
        )
        self.fields["section"].empty_label = None
        self.fields["equipment"].empty_label = None
        self.fields["urgency"].choices = Application.Urgency.choices
        self.fields["equipment"].queryset = Equipment.objects.all()
        self.fields["equipment"].label_from_instance = (
            lambda obj: "Ковш " + format(obj.bucket_volume.normalize(), "f").replace(".", ",")
            + " м³ — " + ("Свободен" if obj.status == Equipment.Status.AVAILABLE else "Не свободен")
        )
        for field in self.fields.values():
            field.required = True
            field.error_messages["required"] = "Заполните это поле."
            if isinstance(field, forms.ModelChoiceField):
                field.error_messages["invalid_choice"] = "Выберите значение из списка."

    def clean_equipment(self):
        equipment = self.cleaned_data["equipment"]
        if equipment.status == Equipment.Status.MAINTENANCE:
            raise forms.ValidationError("Погрузчик на техническом обслуживании.")
        return equipment

    def clean(self):
        cleaned_data = super().clean()
        direction = cleaned_data.get("direction")
        section = cleaned_data.get("section")
        # Проверяем связь на сервере независимо от фильтрации списка в браузере.
        if direction and section and section.direction_id != direction.pk:
            self.add_error("section", "Участок не относится к выбранному направлению.")
        return cleaned_data
