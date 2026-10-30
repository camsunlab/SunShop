from django.shortcuts import render, HttpResponse, get_object_or_404
from .models import TodoItem,PurchaseRequest
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from .forms import SignUpForm, AddRequestForm, AddReceivedForm, OrderedForm
from django.shortcuts import redirect
from django.db.models import Q


# Create your views here.
def home(request):
    return render(request, 'home.html')

def purchase_request(request):
    purchase_requests = PurchaseRequest.objects.filter(status='pending')
    return render(request, 'purchase_request.html', {"purchase_requests": purchase_requests})

def request_record(request,pk):
    request_records = PurchaseRequest.objects.get(id=pk)
    # Look up PurchaseRequest by id
    return render(request, 'request_record.html',{"request_records":request_records})

def all_purchase_(request):
    query = request.GET.get('q', '')
    if query:
        purchase_requests = PurchaseRequest.objects.filter(
            Q(item_name__icontains=query) |
            Q(vendor__icontains=query) |
            Q(project__icontains=query) |
            Q(worktag__icontains=query) |
            Q(requested_by__first_name__icontains=query) |
            Q(requested_by__last_name__icontains=query)
        )
    else:
        purchase_requests = PurchaseRequest.objects.all()

    return render(request, 'all_purchase.html', {'purchase_requests': purchase_requests})

def add_request(request):
    form = AddRequestForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            add_request = form.save()
            messages.success(request, ("Request has been added!"))
            return redirect('purchase_request')
    
    return render(request, 'add_request.html',{"form":form})

def delete_request(request,pk):
    delete_req = PurchaseRequest.objects.get(id=pk)
    delete_req.delete()
    messages.success(request, ("Request has been deleted!"))
    return  redirect('purchase_request') # render(request, 'purchase_request.html')

def order_request(request, pk):
    purchase_request = PurchaseRequest.objects.get(id=pk)
    #purchase_request = get_object_or_404(PurchaseRequest, pk=pk)
    
    if request.method == 'POST':
        form = OrderedForm(request.POST, instance=purchase_request)
        if form.is_valid():
            purchase = form.save(commit=False)
            purchase.status = 'Ordered'
            purchase.save()
            messages.success(request, f"Request #{purchase_request.id} has been ordered.")
            return redirect('purchase_request')
    else:
        form = OrderedForm(instance=purchase_request)

    return render(request, 'order_request.html', {'form': form,'purchase_request': purchase_request})

def pending(request):
    items = TodoItem.objects.all()
    return render(request, 'pending.html',{"todo":items})

def login_user(request):
    if request.method == "POST":
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, ("You have been logged in!"))
            return render(request, 'home.html')
        else:
            messages.error(request, ("Error logging in - please try again..."))
            return render(request, 'login_page.html')
    else:
        return render(request, 'login_page.html')

def logout_user(request):
    logout(request)
    messages.success(request, ("You have been logged out!"))
    return render(request, 'login_page.html')

def register_user(request):
	if request.method == 'POST':
		form = SignUpForm(request.POST)
		if form.is_valid():
			form.save()
			# Authenticate User
			messages.success(request, "You Have Successfully Registered! Welcome!")
			return render(request, 'login_page.html')
	else:
		form = SignUpForm()
		return render(request, 'register.html', {'form':form})

	return render(request, 'register.html', {'form':form})


def update_request(request,pk):
    current_req = PurchaseRequest.objects.get(id=pk)
    form = AddRequestForm(request.POST or None, instance=current_req)
    if form.is_valid():
        form.save()
        messages.success(request, ("Request has been updated!"))
        return redirect('all_purchase') #render(request, 'purchase_request.html')

    return render(request, 'update_request.html',{"form":form})

def add_recieved(request):
    form = AddReceivedForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            add_request = form.save()
            purchase.status = 'Received'
            purchase.save()
            messages.success(request, ("Package Recived!"))
            return redirect('purchase_request')
    
    return render(request, 'add_recieved.html',{"form":form})
