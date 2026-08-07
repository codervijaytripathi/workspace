const https = require('https');

module.exports = async (req, res) => {
  // 1. Sirf POST request allow karein
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method Not Allowed' });
  }

  const { message } = req.body;
  const apiKey = process.env.GROQ_API_KEY;

  // 2. API Key check karein
  if (!apiKey) {
    return res.status(500).json({ error: "Vercel Settings mein GROQ_API_KEY nahi mili!" });
  }

  // 3. Naya Model Data (Jo decommission nahi hua)
  const dataString = JSON.stringify({
    model: "llama-3.3-70b-versatile", 
    messages: [
      { role: "system", content: "You are Agni AI, a helpful assistant." },
      { role: "user", content: message }
    ]
  });

  const options = {
    hostname: 'api.groq.com',
    path: '/openai/v1/chat/completions',
    method: 'POST',
    headers: {
      'Authorization': 'Bearer ' + apiKey,
      'Content-Type': 'application/json',
      'Content-Length': dataString.length
    }
  };

  // 4. API Request bhejein
  const request = https.request(options, (response) => {
    let body = '';
    response.on('data', (chunk) => { body += chunk; });
    response.on('end', () => {
      try {
        const jsonResponse = JSON.parse(body);
        
        if (response.statusCode === 200) {
          res.status(200).json(jsonResponse);
        } else {
          // Groq se aane wala asli error dikhayein
          res.status(response.statusCode).json({ 
            error: jsonResponse.error ? jsonResponse.error.message : "Groq API Error" 
          });
        }
      } catch (e) {
        res.status(500).json({ error: "Response parse karne mein galti hui." });
      }
    });
  });

  request.on('error', (error) => {
    res.status(500).json({ error: "Network Error: " + error.message });
  });

  request.write(dataString);
  request.end();
};
