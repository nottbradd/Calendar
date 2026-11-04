const previews = {
  week: {
    file: "week.png",
    alt: "Calendar's weekly timeline with a 10:15–11:45 lab, overlapping workshops and the upcoming list.",
    caption: "Week view · Timed entries and all-day work.",
  },
  month: {
    file: "calendar.png",
    alt: "November in Calendar, with colour-coded assignments on their dates and an upcoming list alongside.",
    caption: "Month view · Assessments and upcoming deadlines.",
  },
  subjects: {
    file: "settings.png",
    alt: "The Subjects settings showing six saved subjects and eight colour choices for a new subject.",
    caption: "Subjects · Choose the names and colours for your classes.",
  },
};

const options = document.querySelector(".view-options");
const screen = document.getElementById("app-screen");
const imageLink = document.getElementById("image-link");
for (const button of options.querySelectorAll("button")) {
  button.addEventListener("click", () => {
    const view = button.dataset.view;
    const preview = previews[view];
    for (const choice of options.querySelectorAll("button")) {
      choice.setAttribute("aria-pressed", String(choice === button));
    }
    screen.src = preview.file;
    screen.alt = preview.alt;
    screen.classList.toggle("dialog", view === "subjects");
    imageLink.href = preview.file;
    imageLink.setAttribute("aria-label", "Open full-size " + view + " screenshot");
    document.getElementById("full-size").href = preview.file;
    document.getElementById("caption").textContent = preview.caption;
  });
}
options.hidden = false;
