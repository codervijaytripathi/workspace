const rollNumber = document.getElementById("rollNumber");
const searchBtn = document.getElementById("searchBtn");

const studentName = document.getElementById("studentName");
const studentMobile = document.getElementById("studentMobile");
const studentRoll = document.getElementById("studentRoll");
const studentStatus = document.getElementById("studentStatus");

searchBtn.addEventListener("click", async () => {
    const enrollmentNo = rollNumber.value.trim();

    if (!enrollmentNo) {
        alert("Enter Roll Number first");
        rollNumber.focus();
        return;
    }

    searchBtn.disabled = true;
    searchBtn.innerHTML = `
        <i class="ti ti-loader-2"></i>
        Searching...
    `;

    studentName.textContent = "Searching...";
    studentMobile.textContent = "Searching...";
    studentRoll.textContent = enrollmentNo;
    studentStatus.textContent = "Processing...";

    try {
        const response = await fetch("/api/mobile/test", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                enrollment_no: enrollmentNo
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Student lookup failed");
        }

        const result = data.result;

        studentName.textContent =
            result["Candidate Name"] || "Not Found";

        studentMobile.textContent =
            result["Mobile No"] || "Not Found";

        studentRoll.textContent =
            result["Enrollment No"] || enrollmentNo;

        studentStatus.textContent =
            result["Status"] || "Unknown";

    } catch (error) {
        console.error(error);

        studentName.textContent = "—";
        studentMobile.textContent = "—";
        studentRoll.textContent = enrollmentNo;
        studentStatus.textContent = "Error";

        alert(error.message || "Something went wrong");

    } finally {
        searchBtn.disabled = false;

        searchBtn.innerHTML = `
            <i class="ti ti-search"></i>
            Search
        `;
    }
});
