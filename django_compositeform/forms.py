'''
This is awesome. And needs more documentation.

To bring some light in the big number of classes in this file:

First there are:

* ``CompositeForm``
* ``CompositeModelForm``

They are the forms that you probably want to use in your own code. They are
direct base classes of ``django.forms.Form`` and ``django.forms.ModelForm``
and have the formset functionallity of this module backed in. They are ready
to use. Subclass them and be happy.

Then there are:

* ``CompositeFormMixin``
* ``CompositeModelFormMixin``

These are the mixins you can use if you don't want to subclass from
``django.forms.Form`` for whatever reason. The ones with Base at the beginning
don't have a metaclass attached. The ones without the Base in the name have
the relevant metaclass in place that handles the search for
``FormSetField``s.


Here is an example on how you can use this module::

    from django import forms
    from django_compositeform import CompositeModelForm, FormSetField
    from .forms import CommentFormSet


    class PostForm(CompositeModelForm):
        title = forms.CharField()
        text = forms.CharField()
        comments = FormSetField(CommentFormSet)

    # Now you can use the form in the view:

    def post_form(request):
        if request.method == 'POST':
            form = PostForm(request.POST, request.FILES)
            if form.is_valid():
                obj = form.save()
                return HttpResponseRedirect(obj.get_absolute_url())
        else:
            form = PostForm()
        return render_to_response('post_form.html', {
            'form',
        }, context_instance=RequestContext(request))

And yes, thanks for asking, the ``form.is_valid()`` and ``form.save()`` calls
transparantly propagate to the defined comments formset and call their
``is_valid()`` and ``save()`` methods. So you don't have to do anything
special in your view!

Now to how you can access the instantiated formsets::

    >>> form = PostForm()
    >>> form.composite_fields['comments']
    <CommetFormSet: ...>

Or in the template::

    {{ form.as_p }}

    {{ form.composite_fields.comments.management_form }}
    {% for fieldset_form in form.composite_fields.comments %}
        {{ fieldset_form.as_p }}
    {% endfor %}

You're welcome.

'''

from django import forms
from django.forms.forms import DeclarativeFieldsMetaclass, ErrorDict, ErrorList
from django.forms.models import ModelFormMetaclass
from django.utils.datastructures import SortedDict
from django.utils import six
from .fields import CompositeField


def get_declared_composite_fields(bases, attrs):
    """
    Create a list of formset field instances from the passed in 'attrs', plus
    any similar fields on the base classes (in 'bases').
    """
    composite_fields = [
        (field_name, attrs.pop(field_name))
        for field_name, obj in list(six.iteritems(attrs))
        if isinstance(obj, CompositeField)]

    composite_fields.sort(key=lambda x: x[1].creation_counter)

    # If this class is subclassing another Form, add that Form's
    # composite_fields.
    # Note that we loop over the bases in *reverse*. This is necessary in
    # order to preserve the correct order of composite fields.
    for base in bases[::-1]:
        if hasattr(base, 'composite_fields'):
            composite_fields = list(six.iteritems(base.composite_fields)) + composite_fields

    return SortedDict(composite_fields)


class DeclerativeCompositeFieldsMetaclass(type):
    """
    Metaclass that converts FormSetField attributes to a dictionary called
    'composite_fields', taking into account parent class 'composite_fields' as
    well.
    """

    def __new__(cls, name, bases, attrs):
        attrs['composite_fields'] = get_declared_composite_fields(bases, attrs)
        new_class = super(DeclerativeCompositeFieldsMetaclass, cls).__new__(
            cls, name, bases, attrs)
        return new_class


class CompositeFormMixinMetaclass(
        DeclerativeCompositeFieldsMetaclass,
        DeclarativeFieldsMetaclass):
    def __new__(cls, name, bases, attrs):
        attrs['composite_fields'] = get_declared_composite_fields(bases, attrs)
        return DeclarativeFieldsMetaclass.__new__(cls, name, bases, attrs)


class CompositeModelFormMetaclass(
        DeclerativeCompositeFieldsMetaclass,
        ModelFormMetaclass):
    def __new__(cls, name, bases, attrs):
        attrs['composite_fields'] = get_declared_composite_fields(bases, attrs)
        return ModelFormMetaclass.__new__(cls, name, bases, attrs)


class CompositeFormMixin(object):
    '''
    The goal is to provide a mixin that makes handling of formsets and forms on
    forms really easy.

    It should allow something like::

        >>> class MyForm(CompositeFormMixin, forms.Form):
        ...     name = forms.CharField()
        ...     links = FormSetField(formset=LinkFormSet)
        ...
        >>> myform = MyForm()
        >>> isinstance(myform.formsets['links'], LinkFormSet)
        True

    Cleaning, validation, etc should work totally transparent.
    '''

    def __init__(self, *args, **kwargs):
        super(CompositeFormMixin, self).__init__(*args, **kwargs)
        self._init_composite_fields()

    def _init_composite_fields(self):
        '''
        Setup the forms and formsets.
        '''

        self.forms = SortedDict()
        self.formsets = SortedDict()
        for name, field in self.composite_fields.items():
            if hasattr(field, 'get_form'):
                form = field.get_form(self, name)
                self.forms[name] = form
            if hasattr(field, 'get_formset'):
                formset = field.get_formset(self, name)
                self.formsets[name] = formset

    def full_clean(self):
        '''
        Clean the form, including all formsets and add formset errors to the
        errors dict.
        '''

        super(CompositeFormMixin, self).full_clean()
        for key, composite in self.forms.items():
            composite.full_clean()
            if not composite.is_valid():
                self._errors[key] = ErrorDict(composite.errors)
        for key, composite in self.formsets.items():
            composite.full_clean()
            if not composite.is_valid():
                self._errors[key] = ErrorList(composite.errors)


class CompositeModelFormMixin(CompositeFormMixin):
    def save(self, commit=True):
        '''
        If ``commit=False`` django's modelform implementation will attach a
        ``save_m2m`` method to the form instance, so that you can call it
        manually later. When you call ``save_m2m``, the ``save_formsets``
        method will be executed as well.
        '''

        saved_obj = super(CompositeModelFormMixin, self).save(commit=commit)
        self.save_forms(commit=commit)
        self.save_formsets(commit=commit)
        return saved_obj

    def _extend_save_m2m(self, name, composites):
        additional_save_m2m = []
        for composite in composites:
            if hasattr(composite, 'save_m2m'):
                additional_save_m2m.append(composite.save_m2m)

        if not additional_save_m2m:
            return

        def additional_saves():
            for save_m2m in additional_save_m2m:
                save_m2m()

        # The save() method was called before save_formsets(), so we will
        # already have save_m2m() available.
        if hasattr(self, 'save_m2m'):
            _original_save_m2m = self.save_m2m
        else:
            _original_save_m2m = lambda: None
        def augmented_save_m2m():
            _original_save_m2m()
            additional_saves()

        self.save_m2m = augmented_save_m2m
        setattr(self, name, additional_saves)

    def save_forms(self, commit=True):
        saved_composites = []
        for name, composite in self.forms.items():
            field = self.composite_fields[name]
            if hasattr(field, 'save'):
                field.save(self, name, composite, commit=commit)
                saved_composites.append(composite)

        self._extend_save_m2m('save_forms_m2m', saved_composites)

    def save_formsets(self, commit=True):
        '''
        Save all formsets. If ``commit=False``, it will modify the form's
        ``save_m2m()`` so that it also calls the formsets' ``save_m2m()``
        methods.
        '''

        saved_composites = []
        for name, composite in self.formsets.items():
            field = self.composite_fields[name]
            if hasattr(field, 'save'):
                field.save(self, name, composite, commit=commit)
                saved_composites.append(composite)

        self._extend_save_m2m('save_formsets_m2m', saved_composites)


class CompositeModelForm(CompositeModelFormMixin, forms.ModelForm):
    __metaclass__ = CompositeModelFormMetaclass


class CompositeForm(CompositeFormMixin, forms.Form):
    __metaclass__ = CompositeFormMixinMetaclass
