(() => {
  const fallbackCopy = (text) => {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  };

  const copyText = async (text) => {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    fallbackCopy(text);
  };

  document.addEventListener("DOMContentLoaded", () => {
    const inputs = document.querySelectorAll(
      ".code_cell .input, .jp-CodeCell .jp-Cell-inputWrapper, " +
      ".text_cell_render .highlight, .jp-MarkdownOutput .highlight"
    );

    inputs.forEach((input) => {
      if (input.querySelector(".nb-copy-button")) return;
      const code = input.querySelector("pre");
      if (!code) return;

      const button = document.createElement("button");
      button.type = "button";
      button.className = "nb-copy-button";
      button.textContent = "Copy";
      button.setAttribute("aria-label", "Copy code");
      button.addEventListener("click", async () => {
        try {
          await copyText(code.innerText);
          button.textContent = "Copied";
        } catch {
          button.textContent = "Try again";
        }
        window.setTimeout(() => {
          button.textContent = "Copy";
        }, 1200);
      });
      input.appendChild(button);
    });

    const sizeOutputFigure = (image) => {
      const applySize = () => {
        const naturalWidth = Number(image.naturalWidth || 0);
        if (!naturalWidth) return;

        // Notebook PNGs are exported at high DPI. Preserve their relative
        // figure sizes while avoiding both full-column stretching and upscale.
        const targetWidth = Math.min(1100, Math.round(naturalWidth * 0.62));
        image.style.width = `${Math.min(naturalWidth, targetWidth)}px`;
        image.style.maxWidth = "100%";
        image.style.height = "auto";
        image.dataset.figureWidth = targetWidth < 1100 ? "compact" : "wide";
      };

      if (image.complete) {
        applySize();
      } else {
        image.addEventListener("load", applySize, { once: true });
      }
    };

    document.querySelectorAll(".output_png img").forEach(sizeOutputFigure);

    const sectionLinks = Array.from(
      document.querySelectorAll('.nb-subnav a[href^="#"]')
    );
    const observedSections = sectionLinks
      .map((link) => document.getElementById(link.getAttribute("href").slice(1)))
      .filter(Boolean);

    if ("IntersectionObserver" in window && observedSections.length) {
      const setCurrentSection = (id) => {
        sectionLinks.forEach((link) => {
          if (link.getAttribute("href") === `#${id}`) {
            link.setAttribute("aria-current", "location");
          } else {
            link.removeAttribute("aria-current");
          }
        });
      };

      const observer = new IntersectionObserver(
        (entries) => {
          const visible = entries
            .filter((entry) => entry.isIntersecting)
            .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
          if (visible.length) setCurrentSection(visible[0].target.id);
        },
        { rootMargin: "-18% 0px -68% 0px", threshold: 0 }
      );
      observedSections.forEach((section) => observer.observe(section));
    }
  });
})();
