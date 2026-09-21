document.querySelectorAll(".module-equation, .math-inline").forEach((element) => {
  katex.render(element.textContent, element, {
    displayMode: element.classList.contains("module-equation"),
    throwOnError: true,
  });
});
