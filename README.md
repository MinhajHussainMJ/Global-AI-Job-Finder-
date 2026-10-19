# 🌍 Global AI Job Finder

An AI-powered job finder that analyzes your CV, discovers matching jobs worldwide, generates tailored CVs and cover letters, and automatically applies where supported. Track applications, deadlines, and interview updates in one dashboard.

**Supports job markets in Pakistan, Europe, the USA, Canada, and beyond.**

🔗 **Live demo:** https://global-ai-job-finder-production.up.railway.app

---

## ✨ Features

- **CV Analysis** – Upload your CV and let AI extract your skills, experience, and strengths.
- **Worldwide Job Discovery** – Find jobs matched to your profile across multiple regions.
- **Tailored CVs & Cover Letters** – Generate application documents customized for each job.
- **Auto-Apply** – Automatically submit applications on supported platforms.
- **Application Dashboard** – Track applications, deadlines, and interview updates in one place.

## 🧱 Project Structure

```
Global-AI-Job-Finder-/
├── backend/       # Server, API, and AI logic
└── .gitignore
```

## 🚀 Getting Started

### Prerequisites

- Python or Node.js (depending on your backend setup – see `backend/`)
- An API key for your AI provider
- Git

### Installation

```bash
# Clone the repository
git clone https://github.com/MinhajHussainMJ/Global-AI-Job-Finder-.git
cd Global-AI-Job-Finder-/backend

# Install dependencies (use the one that matches your backend)
pip install -r requirements.txt
# or
npm install
```

### Environment Variables

Create a `.env` file inside `backend/`:

```env
AI_API_KEY=your_api_key_here
PORT=8000
```

### Run Locally

```bash
# Python
python app.py
# or Node.js
npm start
```

Then open `http://localhost:8000` in your browser.

## ☁️ Deployment

The app is deployed on [Railway](https://railway.app). Pushes to `main` trigger an automatic production deployment.

## 🗺️ Roadmap

- [ ] More job platforms for auto-apply
- [ ] Email and calendar reminders for deadlines and interviews
- [ ] Interview preparation assistant
- [ ] Multi-language CV generation

## 🤝 Contributing

Contributions, issues, and feature requests are welcome. Feel free to open an issue or submit a pull request.

## 👤 Author

**Minhaj Hussain** – MJ AI Agency

- GitHub: [@MinhajHussainMJ](https://github.com/MinhajHussainMJ)
- LinkedIn: [Minhaj Hussain](https://www.linkedin.com/in/minhaj-hussain-b5714979/)

## 📄 License

This project is licensed under the MIT License.
