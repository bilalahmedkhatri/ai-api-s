import boto3

# Replace these with your actual B2 Application Key ID and Application Key
# Make sure the key has permissions to write/update bucket settings (a master key works best for this)
B2_KEY_ID = '003fdde58025dc30000000001'
B2_APP_KEY = 'K003fUrPp7L58zskXOrRwFgn8c35Lbc'
BUCKET_NAME = 'freevoice-gen' # From your video, this looks like your bucket name

s3 = boto3.client('s3',
    endpoint_url='https://s3.eu-central-003.backblazeb2.com',
    aws_access_key_id=B2_KEY_ID,
    aws_secret_access_key=B2_APP_KEY
)

cors_configuration = {
    'CORSRules': [{
        'AllowedHeaders': ['*'],
        'AllowedMethods': ['GET', 'HEAD'],
        'AllowedOrigins': ['*'], # You can restrict this to ['https://yourdomain.com', 'http://localhost:3000'] for better security later
        'MaxAgeSeconds': 3600
    }]
}

try:
    print(f"Applying CORS configuration to bucket: {BUCKET_NAME}...")
    s3.put_bucket_cors(Bucket=BUCKET_NAME, CORSConfiguration=cors_configuration)
    print("✅ CORS updated successfully! You can now download files from the frontend.")
except Exception as e:
    print("❌ Failed to update CORS:")
    print(e)
