import django.forms as forms

from refugeedata.models import Person


def _preferred_language_label_from_instance(instance):
    return "{}, {}: {}".format(instance.iso_code, instance.description, instance.example_text)


class RegistrationForm(forms.ModelForm):

    photo = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, **kwargs):
        super(RegistrationForm, self).__init__(*args, **kwargs)
        if self.instance and self.instance.id:
            del self.fields["registration_card"]
        self.fields["preferred_lang"].label_from_instance = \
            _preferred_language_label_from_instance

    class Meta:
        model = Person
        fields = [
            "name",
            "preferred_lang",
            "number_of_dependents",
            "needs",
            "phone",
            "email",
            "preferred_contact",
            "registration_card",
        ]

    def clean_preferred_contact(self):
        field_name = "preferred_contact"
        preferred_contact = self.cleaned_data[field_name]
        if preferred_contact == "P":
            self.cleaned_data["phone_preferred"] = True
        elif preferred_contact == "E":
            self.cleaned_data["email_preferred"] = True
        return preferred_contact

    def clean(self):
        data = self.cleaned_data
        if data.get("email_preferred", False) and not data.get("email"):
            error = self.fields["email"].default_error_messages["required"]
            self.add_error("email", error)
        if data.get("phone_preferred", False) and not data.get("phone"):
            error = self.fields["phone"].default_error_messages["required"]
            self.add_error("phone", error)
        return data

    def save(self, commit=True):
        instance = super(RegistrationForm, self).save(commit=False)
        if self.cleaned_data.get("photo"):
            filename = self.cleaned_data["photo"]
            instance.photo.name = filename

        def update_card():
            instance.registration_card.active = True
            instance.registration_card.save()

        if commit:
            instance.save()
            update_card()
        else:
            save_m2m_orig = self.save_m2m

            def save_m2m():
                update_card()
                return save_m2m_orig()

            self.save_m2m = save_m2m
        return instance


class RegistrationFormStage2(forms.ModelForm):

    class Meta:
        model = Person
        fields = [
            "photo",
        ]
