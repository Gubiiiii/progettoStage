const form = document.querySelector("#scanForm");
const input = document.querySelector("#codeInput");
const result = document.querySelector("#result");
const cameraButton = document.querySelector("#cameraButton");
const preview = document.querySelector("#preview");

let scanning = false;
let detector = null;

function setResult(kind, text) {
    result.className = `result ${kind}`;
    result.textContent = text;
}

function personLabel(person) {
    const accessible = person.accessible_required ? " - posto accessibile" : "";
    return `${person.first_name} ${person.last_name} (${person.manual_code})${accessible}`;
}

async function submitCode(code) {
    const cleanCode = code.trim();
    if (!cleanCode) {
        return;
    }

    setResult("idle", "Verifica in corso...");
    input.value = "";

    const response = await fetch("/api/checkin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: cleanCode }),
    });

    const data = await response.json();
    const person = data.participant;

    if (data.status === "checked") {
        setResult("ok", `Ingresso confermato: ${personLabel(person)}`);
    } else if (data.status === "already_checked") {
        setResult("warning", `Codice gia usato: ${personLabel(person)}`);
    } else {
        setResult("error", "Codice non valido o non presente tra gli iscritti.");
    }

    input.focus();
}

form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitCode(input.value);
});

cameraButton.addEventListener("click", async () => {
    if (!("BarcodeDetector" in window)) {
        setResult("warning", "Fotocamera non supportata dal browser. Usa la pistola scanner oppure inserisci il codice manuale.");
        return;
    }

    if (!detector) {
        detector = new BarcodeDetector({ formats: ["qr_code"] });
    }

    const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment" },
    });
    preview.srcObject = stream;
    preview.hidden = false;
    await preview.play();

    scanning = true;
    scanFrame();
});

async function scanFrame() {
    if (!scanning) {
        return;
    }

    try {
        const codes = await detector.detect(preview);
        if (codes.length > 0) {
            scanning = false;
            preview.srcObject.getTracks().forEach((track) => track.stop());
            preview.hidden = true;
            await submitCode(codes[0].rawValue);
            return;
        }
    } catch (error) {
        setResult("error", "Errore durante la lettura dalla fotocamera.");
        scanning = false;
        return;
    }

    requestAnimationFrame(scanFrame);
}

input.focus();
