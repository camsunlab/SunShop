from django import forms
# from .models import PurchaseRequest
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import PurchaseRequest


class SignUpForm(UserCreationForm):
    first_name = forms.CharField(max_length=30, label='First Name', widget=forms.TextInput(attrs={'class': 'form-control','placeholder': 'First Name'}) )
    last_name = forms.CharField(max_length=30, label='Last Name', widget=forms.TextInput(attrs={'class': 'form-control','placeholder': 'Last Name'}))
    email = forms.EmailField(max_length=200, label='Email', widget=forms.TextInput(attrs={'class': 'form-control','placeholder': 'Email Address'}))

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super(SignUpForm, self).__init__(*args, **kwargs)

        self.fields['username'].widget.attrs['class'] = 'form-control'
        self.fields['username'].widget.attrs['placeholder'] = 'Username'
        self.fields['username'].label = 'Username'
        self.fields['username'].help_text = 'Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.'

        self.fields['password1'].widget.attrs['class'] = 'form-control'
        self.fields['password1'].widget.attrs['placeholder'] = 'Password'
        self.fields['password1'].label = 'Password'
        self.fields['password1'].help_text = 'Your password must contain at least 8 characters.'

        self.fields['password2'].widget.attrs['class'] = 'form-control'
        self.fields['password2'].widget.attrs['placeholder'] = 'Confirm Password'
        self.fields['password2'].label = 'Confirm Password'
        self.fields['password2'].help_text = 'Enter the same password as before, for verification.'

        self.fields['first_name'].widget.attrs['class'] = 'form-control'
        self.fields['first_name'].widget.attrs['placeholder'] = 'First Name'
        self.fields['first_name'].label = 'First Name'

        self.fields['last_name'].widget.attrs['class'] = 'form-control'
        self.fields['last_name'].widget.attrs['placeholder'] = 'Last Name'
        self.fields['last_name'].label = 'Last Name'

class FirstNameModelChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.first_name

# Create Add Request Form
class AddRequestForm(forms.ModelForm):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Ordered', 'Ordered'),
        ('Received', 'Received'),
        ('Cancelled', 'Cancelled'),
    ]
    requested_by = FirstNameModelChoiceField( queryset=User.objects.all(), label='Requested By', widget=forms.Select(attrs={ 'placeholder': 'Select requester','class': 'form-control'}))
    item_name = forms.CharField(max_length=200, label='Item Name', widget=forms.TextInput(attrs={'class': 'form-control','placeholder': 'Item Name'}))
    vendor = forms.CharField(max_length=200, label='Vendor', widget=forms.TextInput(attrs={'class': 'form-control','placeholder': 'Vendor'}))
    quantity = forms.IntegerField(label='Quantity', widget=forms.NumberInput(attrs={'class': 'form-control','placeholder': 'Quantity'}))
    price = forms.DecimalField(label='Price', widget=forms.NumberInput(attrs={'class': 'form-control','placeholder': 'Price'}))
    purchase_link = forms.URLField(label='Purchase Link', widget=forms.URLInput(attrs={'class': 'form-control','placeholder': 'Purchase Link'}))
    project = forms.CharField(max_length=200, label='Project', widget=forms.TextInput(attrs={'class': 'form-control','placeholder': 'Project'}))
    status = forms.ChoiceField(choices=STATUS_CHOICES, label='Status', widget=forms.Select(attrs={'class': 'form-control', 'placeholder': 'Status'}))
    worktag = forms.CharField(max_length=20, label='Worktag', required = False, widget=forms.TextInput(attrs={'class': 'form-control','placeholder': 'Worktag'}))
    #purchasepath = forms.CharField(label='Purchase Path', required = False ,widget=forms.TextInput(attrs={'class': 'form-control','placeholder': 'Purchase Path ID'}))
    notes = forms.CharField(label='Notes', required = False, widget=forms.Textarea(attrs={'class': 'form-control','placeholder': 'eg: Grant Numbers, tax exemption,  etc'}))

    # Add order button that updates the order throught he request page

    class Meta:
        model = PurchaseRequest
        fields = ('status','requested_by', 'project','item_name', 'vendor', 'quantity', 'price', 'worktag','purchase_link', 'notes')

# # Create Add Received Form
class AddReceivedForm(forms.ModelForm):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Ordered', 'Ordered'),
        ('Received', 'Received'),
        ('Cancelled', 'Cancelled'),
    ]
    #status = forms.ChoiceField(choices=STATUS_CHOICES, label='Status', widget=forms.Select(attrs={'class': 'form-control', 'placeholder': 'Status'}))
    request = forms.ModelChoiceField(queryset=PurchaseRequest.objects.all(), label='Select Request', widget=forms.Select(attrs={'class': 'form-control', 'placeholder': 'Select Request'}))
    received_by = FirstNameModelChoiceField( queryset=User.objects.all(), label='Requested By', widget=forms.Select(attrs={ 'placeholder': 'Select requester','class': 'form-control'}))
    packing_slip = forms.FileField(label='Packing Slip', widget=forms.FileInput(attrs={'class': 'form-control','placeholder': 'Packing Slip'}))
    #notes = forms.CharField(label='Notes', widget=forms.Textarea(attrs={'class': 'form-control','placeholder': 'Notes'}))

    class Meta:
        model = PurchaseRequest
        fields = ('request','received_by', 'packing_slip')

class OrderedForm(forms.ModelForm):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Ordered', 'Ordered'),
        ('Received', 'Received'),
        ('Cancelled', 'Cancelled'),
    ]
    #status = forms.ChoiceField(choices=STATUS_CHOICES, label='Status', widget=forms.Select(attrs={'class': 'form-control', 'placeholder': 'Status'}))
    purchaser = FirstNameModelChoiceField( queryset=User.objects.all(), label='Requested By', widget=forms.Select(attrs={ 'placeholder': 'Select requester','class': 'form-control'}))
    purchasepath = forms.CharField(label='Purchase Path', widget=forms.TextInput(attrs={'class': 'form-control','placeholder': 'Purchase Path ID'}))

    class Meta:
        model = PurchaseRequest
        fields = ('purchaser', 'purchasepath')
        widgets = {
            'purchaser_notes': forms.Textarea(attrs={'rows': 3}),
        }