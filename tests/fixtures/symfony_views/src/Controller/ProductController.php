<?php

namespace App\Controller;

use Symfony\Bridge\Twig\Attribute\Template;
use Symfony\Bundle\FrameworkBundle\Controller\AbstractController;
use Symfony\Component\HttpFoundation\Response;

class ProductController extends AbstractController
{
    public function list(): Response
    {
        return $this->render('product/index.html.twig', ['items' => []]);
    }

    #[Template('product/index.html.twig')]
    public function show(): array
    {
        return [];
    }

    #[Template(template: 'product/_row.html.twig')]
    public function partial(): array
    {
        return [];
    }

    public function row(): string
    {
        return $this->renderView("product/_row.html.twig");
    }

    public function byName(string $name): Response
    {
        return $this->render('product/'.$name.'.html.twig');
    }

    public function interpolated(string $name): Response
    {
        return $this->render("product/{$name}.html.twig");
    }

    public function missing(): Response
    {
        return $this->render('product/missing.html.twig');
    }
}
