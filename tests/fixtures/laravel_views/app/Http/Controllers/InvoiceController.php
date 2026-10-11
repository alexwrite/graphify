<?php

namespace App\Http\Controllers;

use Illuminate\Support\Facades\View;

class InvoiceController
{
    public function show()
    {
        return view('invoices.show', ['invoice' => null]);
    }

    public function edit()
    {
        return View::make('invoices.show');
    }

    public function nav()
    {
        return response()->view('partials.nav');
    }

    public function byName(string $name)
    {
        return view('invoices.'.$name);
    }

    public function interpolated(string $name)
    {
        return view("invoices.{$name}");
    }

    public function vendor()
    {
        return view('mail::message');
    }

    public function missing()
    {
        return view('invoices.missing');
    }
}
