from django.forms import Form
from django import forms
from django.core import validators


class UploadForm(Form):

    name=forms.CharField(
        label='نام فایل',
        widget=forms.TextInput(attrs={
            'class':"input w-input",
            'data - v - 5fe76b6c' : "",}),
        validators=[
            validators.MaxLengthValidator(100),
        ]
    )

    description = forms.CharField(
        label='توضیحات',
        widget=forms.Textarea(attrs={
            'class':"input w-input",
            'type' : "",
            'data - v - 5fe76b6c' : "",}),
        validators=[
            validators.MaxLengthValidator(100)
        ]
    )
    file = forms.CharField(
        label='فایل',
        widget=forms.FileInput(attrs={
            'class':"input w-input",
            'data - v - 5fe76b6c' : "",}),
    )